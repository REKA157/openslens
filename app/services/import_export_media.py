"""
Import d'un zip d'export WhatsApp AVEC médias.

Le zip WhatsApp natif contient :
  - _chat.txt avec les références "< pièce jointe : XXX-PHOTO-... >"
  - les fichiers médias (.jpg, .mp4, .opus, .pdf, ...) à plat

Pour chaque mention dans le chat, on cherche le fichier correspondant dans
le zip, on l'upload dans Supabase Storage, on crée la ligne whatsapp_media
correspondante, et on lance vision Claude pour les images.

Architecture :
  - Le zip est écrit dans un fichier temporaire (évite de saturer la RAM
    pour les zips > 500 MB)
  - Parsing du chat puis itération sur les médias
  - Upload + vision en concurrence limitée (Anthropic rate-limit)
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, IO

from app.config import settings
from app.db import get_supabase
from app.services import import_export
from app.services.ai import vision as vision_service
from app.services.groups import get_or_create_pilot_group_id

logger = logging.getLogger(__name__)


# Pattern pour extraire le nom de fichier mentionné dans le chat
_ATTACHMENT_FILENAME_RE = re.compile(
    r"<\s*pi[èe]ce\s+jointe\s*:\s*([^>]+?)>",
    re.IGNORECASE,
)


# Mapping nom de fichier → type média
def _detect_media_type(filename: str) -> str:
    name_upper = filename.upper()
    if any(tag in name_upper for tag in ("PHOTO", "IMG", "IMAGE")):
        return "image"
    if any(tag in name_upper for tag in ("VIDEO", "VID", "MOV")):
        return "video"
    if any(tag in name_upper for tag in ("AUDIO", "PTT", "VOICE", "OPUS")):
        return "audio"
    if any(tag in name_upper for tag in ("STK", "STICKER")):
        return "sticker"
    if any(tag in name_upper for tag in ("DOC", "PDF")):
        return "document"
    # Sinon, on devine via extension
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext in {"jpg", "jpeg", "png", "webp", "gif"}:
        return "image"
    if ext in {"mp4", "mov", "avi", "3gp"}:
        return "video"
    if ext in {"opus", "mp3", "m4a", "ogg", "wav"}:
        return "audio"
    if ext in {"pdf", "docx", "xlsx", "doc", "xls", "ppt", "pptx"}:
        return "document"
    return "document"


def _guess_mime(filename: str, media_type: str) -> str:
    """Devine le content-type pour l'upload + vision."""
    guess, _ = mimetypes.guess_type(filename)
    if guess:
        return guess
    # Fallbacks par type
    if media_type == "image":
        return "image/jpeg"
    if media_type == "video":
        return "video/mp4"
    if media_type == "audio":
        return "audio/ogg"
    if media_type == "document":
        return "application/octet-stream"
    return "application/octet-stream"


async def import_zip(
    zip_io: IO[bytes],
    *,
    upload_concurrency: int = 3,
    analyze_images: bool = True,
) -> dict[str, Any]:
    """
    Parse un zip d'export WhatsApp, upload les médias, lance vision si demandé.
    """
    sb = get_supabase()
    group_uuid = get_or_create_pilot_group_id()

    stats: dict[str, Any] = {
        "messages_parsed": 0,
        "media_mentions_in_chat": 0,
        "media_files_in_zip": 0,
        "uploaded": 0,
        "vision_analyzed": 0,
        "skipped_media_not_in_zip": 0,
        "skipped_already_in_db": 0,
        "errors": 0,
        "sample_errors": [],
    }
    sample_errors: list[str] = stats["sample_errors"]

    # Écrire le zip dans un tempfile pour éviter d'avoir tout en RAM
    # (les UploadFile FastAPI font déjà ça normalement, mais on s'assure)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        # Si zip_io supporte read(), on stream par chunks
        while True:
            chunk = zip_io.read(8 * 1024 * 1024)  # 8 MB par chunk
            if not chunk:
                break
            tmp.write(chunk)
        tmp_path = tmp.name

    try:
        with zipfile.ZipFile(tmp_path) as zf:
            namelist = zf.namelist()
            # Trouver _chat.txt
            chat_name = next(
                (n for n in namelist if n.lower().endswith("_chat.txt")),
                None,
            )
            if not chat_name:
                raise ValueError("Aucun _chat.txt trouvé dans le zip")

            # Lire le chat
            with zf.open(chat_name) as f:
                chat_bytes = f.read()
            try:
                chat_text = chat_bytes.decode("utf-8")
            except UnicodeDecodeError:
                chat_text = chat_bytes.decode("utf-8-sig", errors="replace")

            # Index des fichiers médias dans le zip (exclude _chat.txt et metadata)
            media_in_zip: dict[str, str] = {}  # basename → fullname dans zip
            for n in namelist:
                if n.lower().endswith("_chat.txt"):
                    continue
                if n.startswith("__MACOSX/") or "/.DS_Store" in n:
                    continue
                basename = n.rsplit("/", 1)[-1]
                if basename:
                    media_in_zip[basename] = n
            stats["media_files_in_zip"] = len(media_in_zip)

            logger.info(
                "ZIP parsed: %d files, %d media candidates",
                len(namelist), len(media_in_zip),
            )

            # Parser le chat
            parsed_list = list(import_export.parse_export(chat_text))
            stats["messages_parsed"] = len(parsed_list)

            # Pour chaque message avec mention "< pièce jointe : XXX >",
            # on essaie de récupérer le média correspondant.
            # On commence par construire les rows messages (réutilise to_db_row)
            tasks: list[asyncio.Task] = []
            sem = asyncio.Semaphore(upload_concurrency)

            async def process_one(parsed: dict, attachment_basename: str) -> str:
                async with sem:
                    return await _process_one_media(
                        zf,
                        media_in_zip,
                        parsed,
                        attachment_basename,
                        group_uuid,
                        analyze_images,
                        sample_errors,
                    )

            # Boucle : collecter les jobs
            for parsed in parsed_list:
                text = parsed["text"]
                m = _ATTACHMENT_FILENAME_RE.search(text)
                if not m:
                    continue
                filename = m.group(1).strip()
                stats["media_mentions_in_chat"] += 1
                tasks.append(
                    asyncio.create_task(process_one(parsed, filename))
                )

            if not tasks:
                return stats

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    stats["errors"] += 1
                    if len(sample_errors) < 10:
                        sample_errors.append(
                            f"{type(r).__name__}: {r!s}[:200]"
                        )
                    continue
                if r == "uploaded":
                    stats["uploaded"] += 1
                elif r == "uploaded+vision":
                    stats["uploaded"] += 1
                    stats["vision_analyzed"] += 1
                elif r == "not_in_zip":
                    stats["skipped_media_not_in_zip"] += 1
                elif r == "already":
                    stats["skipped_already_in_db"] += 1

    finally:
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return stats


async def _process_one_media(
    zf: zipfile.ZipFile,
    media_in_zip: dict[str, str],
    parsed: dict,
    attachment_basename: str,
    group_uuid: str,
    analyze_images: bool,
    sample_errors: list[str],
) -> str:
    """Traite un (message, fichier média mentionné). Retourne un code de résultat."""
    sb = get_supabase()

    # 1. Trouver le fichier dans le zip
    if attachment_basename not in media_in_zip:
        # Essai par suffixe (parfois les noms ont des espaces ou variants)
        candidates = [
            full for base, full in media_in_zip.items()
            if base.lower() == attachment_basename.lower()
        ]
        if not candidates:
            return "not_in_zip"
        zip_path = candidates[0]
    else:
        zip_path = media_in_zip[attachment_basename]

    media_type = _detect_media_type(attachment_basename)
    mime_type = _guess_mime(attachment_basename, media_type)

    # 2. Construire ou retrouver le whatsapp_message correspondant
    db_row = import_export.to_db_row(
        parsed,
        company_id=settings.company_id,
        group_uuid=group_uuid,
    )
    if db_row is None:
        # message système ou autre → on skip
        return "not_in_zip"

    # Force le bon type pour message si on a détecté un média
    db_row["message_type"] = media_type

    external_id = db_row["external_message_id"]

    # Upsert pour récupérer l'id du message
    upserted = (
        sb.table("whatsapp_messages")
        .upsert(
            db_row,
            on_conflict="company_id,group_id,external_message_id",
        )
        .execute()
    )
    if not upserted.data:
        # Si l'upsert ne retourne rien, on cherche manuellement
        existing = (
            sb.table("whatsapp_messages")
            .select("id")
            .eq("external_message_id", external_id)
            .limit(1)
            .execute()
        )
        if not existing.data:
            return "already"  # bizarre
        message_uuid = existing.data[0]["id"]
    else:
        message_uuid = upserted.data[0]["id"]

    # 3. Vérifier si on a déjà un whatsapp_media pour ce message
    existing_media = (
        sb.table("whatsapp_media")
        .select("id,status,storage_path")
        .eq("message_id", message_uuid)
        .limit(1)
        .execute()
    )
    if existing_media.data and existing_media.data[0].get("status") == "stored":
        return "already"

    # 4. Lire le fichier depuis le zip
    with zf.open(zip_path) as f:
        file_bytes = f.read()

    if len(file_bytes) < 100:
        return "not_in_zip"  # fichier trop petit/corrompu

    # 5. Upload dans Supabase Storage
    ext = "." + attachment_basename.rsplit(".", 1)[-1].lower() if "." in attachment_basename else ""
    date_prefix = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    storage_path = f"raw/{media_type}/{date_prefix}/{message_uuid}{ext}"

    try:
        sb.storage.from_("Media").upload(
            path=storage_path,
            file=file_bytes,
            file_options={
                "content-type": mime_type,
                "upsert": "true",
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Upload échoué pour %s: %s", attachment_basename, exc)
        if len(sample_errors) < 10:
            sample_errors.append(f"upload {attachment_basename}: {exc!s}[:200]")
        raise

    # 6. Créer ou mettre à jour whatsapp_media
    media_row = {
        "message_id": message_uuid,
        "media_type": media_type,
        "mime_type": mime_type,
        "original_filename": attachment_basename,
        "size_bytes": len(file_bytes),
        "storage_path": storage_path,
        "status": "stored",
    }
    if existing_media.data:
        # Update
        media_id = existing_media.data[0]["id"]
        sb.table("whatsapp_media").update(media_row).eq("id", media_id).execute()
    else:
        inserted = sb.table("whatsapp_media").insert(media_row).execute()
        media_id = inserted.data[0]["id"] if inserted.data else None

    # 7. Vision si image
    if media_type == "image" and analyze_images and media_id and mime_type in {
        "image/jpeg", "image/png", "image/gif", "image/webp",
    }:
        try:
            await vision_service.analyze_image(
                media_id=media_id,
                image_bytes=file_bytes,
                mime_type=mime_type,
            )
            return "uploaded+vision"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Vision échoué pour %s: %s", media_id, exc)
            return "uploaded"

    return "uploaded"
