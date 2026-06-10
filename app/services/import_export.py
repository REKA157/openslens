"""
Parser des exports WhatsApp (.txt format natif) et conversion vers le schéma
canonique `whatsapp_messages`.

Format standard WhatsApp (fr) :

    [DD/MM/YYYY HH:MM:SS] Nom prénom: texte
    [DD/MM/YYYY HH:MM:SS] ~ Surnom: texte (contact non enregistré)
    [DD/MM/YYYY HH:MM:SS] Nom: image absente / vidéo absente / audio omis...
    [DD/MM/YYYY HH:MM:SS] ADS Multi Sites: Les messages et les appels sont chiffrés...   ← système, on saute

Les messages multi-lignes continuent jusqu'à la prochaine ligne qui commence
par `[DD/MM/YYYY ...]`. Les caractères Unicode `‎` (U+200E, LRM) que WhatsApp
insère devant les médias et mentions sont nettoyés.

Comme l'export n'a pas de message_id WhatsApp, on génère un identifiant
déterministe `export:<sha1>` à partir de (date+sender+début du texte) →
permet l'upsert idempotent (réimporter le même fichier ne crée pas de doublons).
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

try:
    from zoneinfo import ZoneInfo
    _PARIS = ZoneInfo("Europe/Paris")
except Exception:  # noqa: BLE001 — fallback si tzdata indispo
    _PARIS = timezone.utc

logger = logging.getLogger(__name__)


# --- Format ---

_LINE_RE = re.compile(
    r"^\[(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\] ([^:]+?): (.*)$",
    re.DOTALL,
)
_LRM = "‎"  # Left-to-right mark inséré par WhatsApp devant médias/mentions
_FSI = "⁨"  # First strong isolate (autour des @mentions)
_PDI = "⁩"  # Pop directional isolate
_CONTROL_CHARS = (_LRM, _FSI, _PDI)


# --- Détection média / système (français) ---

# Format "exporté SANS médias" : « ‎image absente », « audio omis »...
MEDIA_MARKERS: dict[str, str] = {
    "image absente": "image",
    "image omise": "image",
    "vidéo absente": "video",
    "vidéo omise": "video",
    "video absente": "video",
    "video omise": "video",
    "GIF absent": "video",
    "GIF omis": "video",
    "audio omis": "audio",
    "audio absent": "audio",
    "document omis": "document",
    "document absent": "document",
    "sticker omis": "sticker",
    "sticker absent": "sticker",
}

# Format "exporté AVEC médias" : « < pièce jointe : 00000007-PHOTO-...jpg > »
# Le nom de fichier indique le type (PHOTO/VIDEO/AUDIO/STICKER/GIF).
MEDIA_ATTACHMENT_RE = re.compile(
    r"<\s*pi[èe]ce\s+jointe\s*:\s*([^>]+?)>",
    re.IGNORECASE,
)

# Mapping nom de fichier → type canonique
_ATTACHMENT_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("PHOTO", "image"),
    ("IMG", "image"),
    ("VIDEO", "video"),
    ("VID", "video"),
    ("AUDIO", "audio"),
    ("PTT", "audio"),
    ("STICKER", "sticker"),
    ("GIF", "video"),
    ("DOC", "document"),
    ("PDF", "document"),
    (".jpg", "image"),
    (".jpeg", "image"),
    (".png", "image"),
    (".mp4", "video"),
    (".mp3", "audio"),
    (".opus", "audio"),
    (".webp", "sticker"),
)


SYSTEM_FRAGMENTS = (
    "a créé ce groupe",
    "a ajouté·e",
    "vous a ajouté",
    "a été ajouté",
    "Les messages et les appels sont chiffrés",
    "a changé le sujet",
    "a changé l'icône",
    "a quitté",
    "Ce message a été supprimé",
    "vous a expulsé",
    "vous a retiré",
    "ne fait plus partie",
    "a été retiré",
)


# --- Parsing -----------------------------------------------------------------


def _clean_controls(s: str) -> str:
    for c in _CONTROL_CHARS:
        s = s.replace(c, "")
    return s


def parse_export(text: str) -> Iterable[dict]:
    """
    Yield un dict {dt, sender, text, raw_line} par message WhatsApp.
    Gère les messages multi-lignes.
    """
    current: dict | None = None
    for raw_line in text.splitlines():
        line = raw_line.lstrip(_LRM)  # parfois les lignes média commencent par U+200E
        m = _LINE_RE.match(line)
        if m:
            if current is not None:
                yield current
            dt_str, sender, body = m.group(1), m.group(2), m.group(3)
            try:
                local_dt = datetime.strptime(dt_str, "%d/%m/%Y %H:%M:%S").replace(tzinfo=_PARIS)
            except ValueError:
                logger.warning("Date illisible : %r — ligne ignorée", dt_str)
                current = None
                continue
            current = {
                "dt": local_dt.astimezone(timezone.utc),
                "sender": _clean_controls(sender.strip()),
                "text": _clean_controls(body.strip()),
            }
        elif current is not None:
            # Continuation du message précédent
            current["text"] = (current["text"] + "\n" + _clean_controls(raw_line)).strip()
    if current is not None:
        yield current


# --- Mapping vers le schéma DB ----------------------------------------------


def _classify_type(text: str) -> tuple[str, str | None]:
    """
    À partir du texte brut d'un message, renvoie :
      - ('system', None) si système (à skipper)
      - ('image'|'video'|'audio'|'document'|'sticker', caption_or_none) si média
      - ('text', text) sinon
    """
    for fragment in SYSTEM_FRAGMENTS:
        if fragment in text:
            return ("system", None)

    # Export AVEC médias : « < pièce jointe : XXX-PHOTO-...jpg > »
    m = MEDIA_ATTACHMENT_RE.search(text)
    if m:
        filename = m.group(1).strip()
        mtype = "image"  # défaut
        for hint, t in _ATTACHMENT_TYPE_HINTS:
            if hint.lower() in filename.lower():
                mtype = t
                break
        caption = MEDIA_ATTACHMENT_RE.sub("", text).strip(" \n\t-—:")
        return (mtype, caption or None)

    # Export SANS médias : « image absente », « audio omis »...
    for marker, mtype in MEDIA_MARKERS.items():
        if marker in text:
            caption = text.replace(marker, "").strip(" \n\t-—:")
            return (mtype, caption or None)

    return ("text", text)


def normalize_for_match(raw_text: str | None) -> str:
    """
    Normalise un texte pour le matching cross-export robuste :
    - Strip pattern « < pièce jointe : ... > » (export avec médias)
    - Strip whitespace, lower
    - Tronque à 25 chars (assez discriminant, robuste aux différences mineures)
    """
    if not raw_text:
        return ""
    s = MEDIA_ATTACHMENT_RE.sub("", raw_text)
    s = " ".join(s.split())  # collapse whitespace
    return s.strip().lower()[:25]


def _normalize_sender(sender: str) -> str:
    # Les contacts non enregistrés ont un "~ " devant
    s = sender.lstrip("~").strip()
    # Espaces multiples
    s = re.sub(r"\s+", " ", s)
    return s


def _make_message_id(dt: datetime, sender: str, text: str) -> str:
    fingerprint = f"{dt.isoformat()}|{sender}|{text[:200]}"
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:24]
    return f"export:{digest}"


def to_db_row(
    parsed: dict,
    *,
    company_id: str,
    group_uuid: str,
    skip_senders: set[str] | None = None,
) -> dict[str, Any] | None:
    """
    Convertit un message parsé en ligne `whatsapp_messages`.
    Retourne None pour les messages à ignorer (système, sender exclu).
    """
    skip_senders = skip_senders or {"ADS Multi Sites"}

    sender = _normalize_sender(parsed["sender"])
    if sender in skip_senders:
        return None

    mtype, raw_text = _classify_type(parsed["text"])
    if mtype == "system":
        return None

    dt: datetime = parsed["dt"]
    return {
        "company_id": company_id,
        "group_id": group_uuid,
        "external_message_id": _make_message_id(dt, sender, parsed["text"]),
        "sender_phone": None,  # pas exposé dans l'export
        "sender_display_name": sender,
        "message_type": mtype,
        "raw_text": raw_text,
        "sent_at": dt.isoformat(),
        "raw_payload": {"source": "whatsapp_export"},
    }
