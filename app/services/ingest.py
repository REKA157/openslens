"""
Logique d'ingestion d'un événement WAHA (webhook ou backfill).

Responsabilités :
 1. Filtrer dur sur PILOT_GROUP_ID — tout autre chat est jeté immédiatement.
 2. Logger le payload brut dans raw_webhooks (audit + debug).
 3. Normaliser en ligne canonique whatsapp_messages (upsert idempotent).
 4. Si média, insérer une ligne whatsapp_media en statut "pending" et
    lancer le téléchargement vers Supabase Storage.

Compatible avec le moteur WAHA WEBJS (la plupart des champs sont dans
`payload._data`).
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.db import get_supabase
from app.services import media as media_service
from app.services.groups import get_or_create_pilot_group_id

logger = logging.getLogger(__name__)

MESSAGE_EVENTS = {"message", "message.any"}

# Préfixes base64 que WAHA WEBJS met dans `body` pour les images/PNG :
# on les filtre pour ne pas polluer raw_text.
_BASE64_PREFIXES = ("/9j/", "iVBORw0KGgo", "R0lGOD")

_MEDIA_TYPES = {"image", "audio", "ptt", "voice", "video", "document", "sticker"}


async def handle_waha_event(payload: dict[str, Any]) -> dict:
    """
    Point d'entrée pour un événement webhook WAHA.
    Retourne un dict de diagnostic.
    """
    event = payload.get("event")
    data = payload.get("payload") or {}

    sb = get_supabase()

    # 1. Audit brut systématique
    sb.table("raw_webhooks").insert(
        {"source": "waha", "payload": payload, "processed": False}
    ).execute()

    # 2. Filtre type d'événement
    if event not in MESSAGE_EVENTS:
        logger.debug("Ignored non-message event: %s", event)
        return {"status": "ignored", "reason": f"event {event} not handled"}

    # 3. Filtre groupe pilote
    chat_id = _extract_chat_id(data)
    if chat_id != settings.pilot_group_id:
        logger.debug("Ignored message from non-pilot chat: %s", chat_id)
        return {"status": "ignored", "reason": "chat not in pilot scope"}

    # 4. Normalise + upsert message
    msg_row = _normalize_message(data)
    if msg_row is None:
        logger.warning("Could not normalize message")
        return {"status": "skipped", "reason": "missing required fields"}

    inserted = (
        sb.table("whatsapp_messages")
        .upsert(msg_row, on_conflict="company_id,group_id,external_message_id")
        .execute()
    )
    if not inserted.data:
        logger.warning("Upsert returned no rows for %s", msg_row["external_message_id"])
        return {"status": "error", "reason": "upsert returned no data"}

    message_uuid = inserted.data[0]["id"]

    # 5. Médias
    if _has_media(data):
        media_row = _build_media_row(message_uuid, data)
        sb.table("whatsapp_media").insert(media_row).execute()
        try:
            await media_service.download_and_store(
                message_uuid=message_uuid,
                waha_message_id=msg_row["external_message_id"],
                payload=data,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Media download failed for %s: %s",
                             msg_row["external_message_id"], exc)

    return {"status": "stored", "message_id": message_uuid}


# --------------------------- helpers ---------------------------


def _data_section(data: dict[str, Any]) -> dict[str, Any]:
    """Helper : récupère payload._data (vide si absent)."""
    inner = data.get("_data")
    return inner if isinstance(inner, dict) else {}


def _extract_chat_id(data: dict[str, Any]) -> str | None:
    """ID du chat (groupe ou DM)."""
    chat_id = data.get("from")
    if isinstance(chat_id, str):
        return chat_id
    if isinstance(chat_id, dict):
        return chat_id.get("_serialized")
    # Fallback id.remote
    msg_id = data.get("id") or {}
    if isinstance(msg_id, dict):
        remote = msg_id.get("remote")
        if isinstance(remote, str):
            return remote
        if isinstance(remote, dict):
            return remote.get("_serialized")
    return None


def _extract_external_message_id(data: dict[str, Any]) -> str | None:
    msg_id = data.get("id")
    if isinstance(msg_id, str):
        return msg_id
    if isinstance(msg_id, dict):
        return msg_id.get("_serialized") or msg_id.get("id")
    return None


def _extract_sender_phone(data: dict[str, Any]) -> str | None:
    """
    Pour les messages de groupe, `from` est le group_id. L'auteur réel est
    dans `participant` (WEBJS top-level) ou `_data.author`.
    """
    chat_id = data.get("from")
    is_group = isinstance(chat_id, str) and chat_id.endswith("@g.us")

    if is_group:
        # WEBJS : participant au top level
        participant = data.get("participant")
        if isinstance(participant, str):
            return participant.split("@")[0] if "@" in participant else participant
        # WEBJS bis : _data.author (string)
        author = _data_section(data).get("author")
        if isinstance(author, str):
            return author.split("@")[0] if "@" in author else author
        # NOWEB : author objet
        if isinstance(author, dict):
            return author.get("user")
        return None

    # DM : from = sender
    if isinstance(chat_id, str):
        return chat_id.split("@")[0] if "@" in chat_id else chat_id
    return None


def _extract_display_name(data: dict[str, Any]) -> str | None:
    inner = _data_section(data)
    return (
        inner.get("notifyName")
        or data.get("notifyName")
        or inner.get("pushName")
        or data.get("pushName")
    )


def _extract_timestamp(data: dict[str, Any]) -> str | None:
    ts = (
        data.get("timestamp")
        or data.get("t")
        or _data_section(data).get("t")
    )
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _get_waha_type(data: dict[str, Any]) -> str | None:
    """Le `type` WAHA est en général dans _data.type (WEBJS)."""
    t = _data_section(data).get("type") or data.get("type")
    return (t or "").lower() or None


def _map_message_type(waha_type: str | None) -> str:
    return {
        "chat": "text",
        "text": "text",
        "image": "image",
        "audio": "audio",
        "ptt": "audio",
        "voice": "audio",
        "video": "video",
        "document": "document",
        "location": "location",
        "vcard": "contact",
        "contact_card": "contact",
        "sticker": "sticker",
        "revoked": "system",
        "notification_template": "system",
    }.get(waha_type or "", "mixed")


def _has_media(data: dict[str, Any]) -> bool:
    if data.get("hasMedia") is True:
        return True
    if data.get("media"):
        return True
    return _get_waha_type(data) in _MEDIA_TYPES


def _clean_text(text: Any) -> str | None:
    """Filtre les thumbnails base64 que WAHA WEBJS met dans `body` pour les médias."""
    if not isinstance(text, str) or not text:
        return None
    stripped = text.strip()
    for prefix in _BASE64_PREFIXES:
        if stripped.startswith(prefix):
            return None
    return text


def _extract_raw_text(data: dict[str, Any], canonical_type: str) -> str | None:
    inner = _data_section(data)

    # Texte pur : body est la vraie donnée
    if canonical_type in ("text", "mixed"):
        text = _clean_text(data.get("body"))
        if text:
            return text

    # Média : la légende est dans _data.caption
    caption = inner.get("caption") or data.get("caption")
    return _clean_text(caption)


def _normalize_message(data: dict[str, Any]) -> dict | None:
    external_id = _extract_external_message_id(data)
    sent_at = _extract_timestamp(data)
    if not external_id or not sent_at:
        return None

    group_uuid = get_or_create_pilot_group_id()
    waha_type = _get_waha_type(data)
    canonical_type = _map_message_type(waha_type)

    return {
        "company_id": settings.company_id,
        "group_id": group_uuid,
        "external_message_id": external_id,
        "sender_phone": _extract_sender_phone(data),
        "sender_display_name": _extract_display_name(data),
        "message_type": canonical_type,
        "raw_text": _extract_raw_text(data, canonical_type),
        "sent_at": sent_at,
        "raw_payload": data,
    }


def _build_media_row(message_uuid: str, data: dict[str, Any]) -> dict:
    media = data.get("media") or {}
    inner = _data_section(data)
    size = inner.get("size")
    duration = inner.get("duration")
    return {
        "message_id": message_uuid,
        "media_type": _map_media_type(_get_waha_type(data)),
        "mime_type": media.get("mimetype") or inner.get("mimetype"),
        "original_filename": media.get("filename") or inner.get("filename"),
        "size_bytes": size if isinstance(size, int) else None,
        "duration_seconds": duration if isinstance(duration, int) else None,
        "status": "pending",
    }


def _map_media_type(waha_type: str | None) -> str:
    return {
        "image": "image",
        "audio": "audio",
        "ptt": "audio",
        "voice": "audio",
        "video": "video",
        "document": "document",
        "sticker": "sticker",
    }.get(waha_type or "", "document")
