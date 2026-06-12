"""
Transcription des notes vocales WhatsApp via OpenAI Whisper.

Les chauffeurs envoient souvent des vocaux ("benne pleine au Plessis, on fait
quoi ?"). Sans transcription, ce contenu était totalement invisible pour
OpsLens. Ici on transcrit, on stocke dans `audio_transcription`, et le texte
est fusionné dans la classification du message.

Whisper accepte ogg/oga/opus (format natif des vocaux WhatsApp), mp3, m4a, wav…
"""

from __future__ import annotations

import logging

from app.config import settings
from app.db import get_supabase

logger = logging.getLogger(__name__)

# Limite raisonnable (Whisper accepte jusqu'à 25 MB).
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


async def transcribe_audio(
    media_id: str,
    file_bytes: bytes,
    mime_type: str,
    filename: str | None = None,
) -> dict | None:
    """
    Transcrit une note vocale, stocke dans audio_transcription, et renvoie un
    dict (kind='audio') pour fusion dans la classification.
    Best-effort : renvoie None sans lever en cas d'échec.
    """
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY absente, skip transcription audio")
        return None
    if not file_bytes or len(file_bytes) < 100:
        return None
    if len(file_bytes) > _MAX_AUDIO_BYTES:
        logger.warning("Audio trop volumineux (%d B), skip transcription", len(file_bytes))
        return None

    # Whisper exige un nom de fichier avec extension reconnue.
    name = filename or "voice.ogg"
    if "." not in name:
        name += ".ogg"

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        try:
            resp = await client.audio.transcriptions.create(
                model=settings.transcription_model,
                file=(name, file_bytes, mime_type or "audio/ogg"),
            )
        finally:
            await client.close()
        transcript = (getattr(resp, "text", None) or "").strip()
        language = getattr(resp, "language", None)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Transcription audio échouée pour %s: %s", media_id, exc)
        return None

    if not transcript:
        return None

    row = {
        "media_id": media_id,
        "transcript": transcript[:8000],
        "language": language,
        "model_used": settings.transcription_model,
    }
    try:
        sb = get_supabase()
        sb.table("audio_transcription").upsert(row, on_conflict="media_id").execute()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Stockage audio_transcription échoué pour %s: %s", media_id, exc)

    logger.info("Audio transcrit %s: %d caractères", media_id, len(transcript))
    return {**row, "kind": "audio"}
