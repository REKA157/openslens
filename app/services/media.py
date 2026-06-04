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
from app.waha import WahaClient

logger = logging.getLogger(__name__)

BUCKET = "Media"


async def download_and_store(
    message_uuid: str,
    waha_message_id: str,
    payload: dict[str, Any],
) -> None:
    """
    Télécharge le média associé au message et le pousse dans Supabase Storage,
    puis met à jour la ligne whatsapp_media (status=stored, storage_path).
    """
    sb = get_supabase()
    waha = WahaClient()

    try:
        media = payload.get("media") or {}
        media_url = media.get("url")

        # 1. Téléchargement
        if media_url:
            content, content_type = await waha.download_url(media_url)
        else:
            content, content_type = await waha.download_media(waha_message_id)

        # 2. Construction du chemin Storage
        media_type = (payload.get("type") or "document").lower()
        if media_type in {"ptt", "voice"}:
            folder = "audio"
        elif media_type in {"image", "audio", "video", "document", "sticker"}:
            folder = media_type
        else:
            folder = "other"

        ext = _guess_extension(media.get("filename"), content_type)
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
        sb.table("whatsapp_media").update(
            {
                "storage_path": storage_path,
                "status": "stored",
                "size_bytes": len(content),
            }
        ).eq("message_id", message_uuid).execute()

        logger.info("Media stored: %s (%d bytes)", storage_path, len(content))

    except Exception as exc:  # noqa: BLE001
        logger.exception("download_and_store failed: %s", exc)
        sb.table("whatsapp_media").update(
            {"status": "failed", "download_error": str(exc)[:500]}
        ).eq("message_id", message_uuid).execute()
        raise
    finally:
        await waha.aclose()


def _guess_extension(filename: str | None, content_type: str) -> str:
    """Devine l'extension du fichier, ex: '.jpg', '.ogg', '.pdf'."""
    if filename and "." in filename:
        return "." + filename.rsplit(".", 1)[-1].lower()
    ext = mimetypes.guess_extension(content_type or "")
    return ext or ""
