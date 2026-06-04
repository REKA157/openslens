"""
Logique d'ingestion d'un événement WAHA (webhook ou backfill).

Responsabilités :
 1. Filtrer dur sur PILOT_GROUP_ID — tout autre chat est jeté immédiatement.
 2. Logger le payload brut dans raw_webhooks (audit + debug).
 3. Normaliser en ligne canonique whatsapp_messages (upsert idempotent).
 4. Si média, insérer une ligne whatsapp_media en statut "pending" et
    lancer le téléchargement vers Supabase Storage.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.db import get_supabase
from app.services import media as media_service
from app.services.groups import get_or_create_pilot_group_id

logger = logging.getLogger(__name__)


# Types d'événements WAHA qu'on traite comme "nouveau message"
MESSAGE_EVENTS = {"message", "message.any"}


async def handle_waha_event(payload: dict[str, Any]) -> dict:
    """
    Point d'entrée pour un événement webhook WAHA.
    Retourne un dict de diagnostic ({"status": "...", "reason": "..."}).
    """
    event = payload.get("event")
    data = payload.get("payload") or {}

    # 1. Audit brut systématique
    sb = get_supabase()
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
        logger.warning("Could not normalize message: %s", data.get("id"))
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


def _extract_chat_id(data: dict[str, Any]) -> str | None:
    """L'ID du chat (groupe) dans le payload WAHA NOWEB."""
    # NOWEB : "from" contient le chat id pour les messages reçus en groupe
    chat_id = data.get("from")
    if isinstance(chat_id, str):
        return chat_id
    if isinstance(chat_id, dict):
        return chat_id.get("_serialized")
    # Fallback : id.remote
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
    """Pour un groupe, l'auteur est dans 'author' (pas 'from' qui est le groupe)."""
    author = data.get("author")
    if isinstance(author, str):
        return author.split("@")[0] if "@" in author else author
    if isinstance(author, dict):
        return author.get("user")
    # Fallback DM
    sender = data.get("from")
    if isinstance(sender, str):
        return sender.split("@")[0] if "@" in sender else sender
    return None


def _extract_timestamp(data: dict[str, Any]) -> str | None:
    ts = data.get("timestamp") or data.get("t")
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _map_message_type(waha_type: str | None) -> str:
    """Mappe les types WAHA vers nos types canoniques (cf. schéma SQL)."""
    return {
        "chat": "text",
        "text": "text",
        "image": "image",
        "audio": "audio",
        "ptt": "audio",        # push-to-talk = vocal
        "voice": "audio",
        "video": "video",
        "document": "document",
        "location": "location",
        "vcard": "contact",
        "contact_card": "contact",
        "sticker": "sticker",
        "revoked": "system",
        "notification_template": "system",
    }.get((waha_type or "").lower(), "mixed")


def _has_media(data: dict[str, Any]) -> bool:
    if data.get("hasMedia") is True:
        return True
    if data.get("media"):
        return True
    if (data.get("type") or "").lower() in {"image", "audio", "ptt", "voice", "video", "document", "sticker"}:
        return True
    return False


def _normalize_message(data: dict[str, Any]) -> dict | None:
    external_id = _extract_external_message_id(data)
    sent_at = _extract_timestamp(data)
    if not external_id or not sent_at:
        return None

    group_uuid = get_or_create_pilot_group_id()

    reply_id = None
    quoted = data.get("quotedMsgId") or data.get("hasQuotedMsg")
    if isinstance(quoted, str):
        reply_id = quoted

    return {
        "company_id": settings.company_id,
        "group_id": group_uuid,
        "external_message_id": external_id,
        "sender_phone": _extract_sender_phone(data),
        "sender_display_name": data.get("notifyName") or data.get("pushName"),
        "message_type": _map_message_type(data.get("type")),
        "raw_text": data.get("body") or (data.get("caption") if data.get("caption") else None),
        "reply_to_external_message_id": reply_id,
        "sent_at": sent_at,
        "raw_payload": data,
    }


def _build_media_row(message_uuid: str, data: dict[str, Any]) -> dict:
    media = data.get("media") or {}
    return {
        "message_id": message_uuid,
        "media_type": _map_media_type(data.get("type")),
        "mime_type": media.get("mimetype") or media.get("mime_type"),
        "original_filename": media.get("filename"),
        "size_bytes": media.get("size") if isinstance(media.get("size"), int) else None,
        "duration_seconds": data.get("duration") if isinstance(data.get("duration"), int) else None,
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
    }.get((waha_type or "").lower(), "document")
