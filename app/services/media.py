"""
Téléchargement des médias WhatsApp depuis WAHA et upload vers Supabase Storage.
Bucket cible : "Media" (créé manuellement dans Supabase, voir README).

Arborescence :
  raw/{type}/{YYYY-MM-DD}/{message_uuid}.{ext}
"""

import logging
import mimetypes
from datetime import datetime, timezone
from typing import Any

from app.db import get_supabase
from app.services.ai import audio as audio_service
from app.services.ai import document as document_service
from app.services.ai import vision as vision_service
from app.waha import WahaClient

logger = logging.getLogger(__name__)

BUCKET = "Media"


def _resolve_type(payload: dict[str, Any]) -> str:
    """Le type WAHA est dans payload._data.type pour WEBJS."""
    inner = payload.get("_data") or {}
    t = (inner.get("type") or payload.get("type") or "").lower()
    return t or "document"


async def download_and_store(
    message_uuid: str,
    waha_message_id: str,
    payload: dict[str, Any],
) -> dict | None:
    """
    Télécharge le média, l'upload dans Storage, et si c'est une image lance
    la vision Claude. Retourne le dict d'analyse vision (pour que l'appelant
    puisse fusionner la description de la photo dans la classification), ou
    None si pas d'image / vision indisponible.
    """
    sb = get_supabase()
    waha = WahaClient()
    media_analysis: dict | None = None

    try:
        media = payload.get("media") or {}
        media_url = media.get("url")

        # 1. Téléchargement
        if media_url:
            content, content_type = await waha.download_url(media_url)
        else:
            content, content_type = await waha.download_media(waha_message_id)

        # 2. Choix du dossier
        data_type = _resolve_type(payload)
        if data_type in {"ptt", "voice"}:
            folder = "audio"
        elif data_type in {"image", "audio", "video", "document", "sticker"}:
            folder = data_type
        else:
            folder = "other"

        ext = _guess_extension(
            media.get("filename") or (payload.get("_data") or {}).get("filename"),
            content_type,
        )
        date_prefix = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        storage_path = f"raw/{folder}/{date_prefix}/{message_uuid}{ext}"

        # 3. Upload
        sb.storage.from_(BUCKET).upload(
            path=storage_path,
            file=content,
            file_options={
                "content-type": content_type,
                "upsert": "true",
            },
        )

        # 4. Update whatsapp_media
        updated = sb.table("whatsapp_media").update(
            {
                "storage_path": storage_path,
                "status": "stored",
                "size_bytes": len(content),
            }
        ).eq("message_id", message_uuid).execute()

        logger.info("Media stored: %s (%d bytes)", storage_path, len(content))

        # 5. Analyse du contenu selon le type, pour fusion dans la classification.
        if updated.data:
            media_uuid = updated.data[0]["id"]
            fname = media.get("filename") or (payload.get("_data") or {}).get("filename")
            try:
                if data_type == "image":
                    media_analysis = await vision_service.analyze_image(
                        media_id=media_uuid,
                        image_bytes=content,
                        mime_type=content_type,
                    )
                    if media_analysis:
                        media_analysis["kind"] = "image"
                elif data_type == "document":
                    media_analysis = await document_service.analyze_document(
                        media_id=media_uuid,
                        file_bytes=content,
                        mime_type=content_type,
                        filename=fname,
                    )
                elif data_type in {"audio", "ptt", "voice"}:
                    media_analysis = await audio_service.transcribe_audio(
                        media_id=media_uuid,
                        file_bytes=content,
                        mime_type=content_type,
                        filename=fname,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Analyse média (%s) échouée pour %s: %s",
                                 data_type, media_uuid, exc)

        return media_analysis

    except Exception as exc:  # noqa: BLE001
        logger.exception("download_and_store failed: %s", exc)
        sb.table("whatsapp_media").update(
            {"status": "failed", "download_error": str(exc)[:500]}
        ).eq("message_id", message_uuid).execute()
        raise
    finally:
        await waha.aclose()


def _guess_extension(filename: str | None, content_type: str) -> str:
    if filename and "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    ext = mimetypes.guess_extension(content_type or "")
    return ext or ""
