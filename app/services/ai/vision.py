"""
Analyse visuelle d'une image WhatsApp via Claude Sonnet vision.

Pour chaque image téléchargée dans Supabase Storage :
  - Description visuelle factuelle (1-2 phrases)
  - OCR du texte visible (panneaux, plaques, bons, factures...)
  - Type d'image : preuve d'intervention, document photographié, anomalie, etc.
  - Détection d'objets pertinents (camion, benne, site...)
  - Détection d'anomalie potentielle

Stockage : table `image_analysis`.

On utilise Sonnet (et non Haiku) pour la vision : sur des photos terrain
souvent floues, mal cadrées ou avec texte en partie illisible, la qualité
de l'analyse fait toute la différence.
"""

import base64
import json
import logging

import anthropic

from app.config import settings
from app.db import get_supabase

logger = logging.getLogger(__name__)


VISION_MODEL = "claude-sonnet-4-5"


SYSTEM_PROMPT = """Tu es un analyseur visuel de photos terrain pour une entreprise \
de collecte/évacuation de déchets en Île-de-France (PVC, ferraille, alu, bennes \
grutables, chantiers). Les photos sont prises par les chauffeurs et chefs d'équipe.

Ton rôle : transformer une image en JSON structuré exploitable. Tu décris ce que \
tu VOIS, factuellement, sans inventer.

Règles strictes :
1. Tu réponds UNIQUEMENT par un objet JSON conforme au schéma. Aucun texte avant/après.
2. Si une info n'est pas visible ou pas certaine, mets null. Jamais d'invention.
3. `visual_description` : 1-2 phrases factuelles en français, neutres, sans jugement.
4. `ocr_text` : transcris TOUT le texte lisible dans l'image (panneaux, plaques, \
   bons de livraison, factures, écrans...). Si rien : null.
5. `confidence` : 0.0 (très incertain) à 1.0 (très sûr).

Schéma JSON :
{
  "visual_description": "<phrase factuelle>",
  "ocr_text": "<texte lisible ou null>",
  "detected_objects": ["<objets simples>"],
  "image_type": "preuve_intervention | document_photo | facture | bon_livraison | \
anomalie | materiel | site | vehicule | autre",
  "possible_anomaly": true/false,
  "anomaly_description": "<si anomalie ou null>",
  "confidence": 0.0-1.0
}"""


async def analyze_image(media_id: str, image_bytes: bytes, mime_type: str) -> dict | None:
    """
    Analyse une image via Claude Sonnet vision, stocke le résultat dans image_analysis.
    """
    if not settings.anthropic_api_key:
        logger.warning("ANTHROPIC_API_KEY non configurée, skip vision")
        return None

    if not image_bytes or len(image_bytes) < 100:
        logger.debug("Image vide ou trop petite, skip vision")
        return None

    # Claude vision a une limite de taille — on tronque les très grosses images
    # (Claude accepte jusqu'à ~5 MB pour la version base64)
    if len(image_bytes) > 5 * 1024 * 1024:
        logger.warning("Image > 5 MB, skip vision (taille %d B)", len(image_bytes))
        return None

    # Normalise le mime_type (Claude n'accepte que certains formats)
    allowed = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    if mime_type not in allowed:
        logger.debug("MIME type %s non supporté par Claude vision", mime_type)
        return None

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=VISION_MODEL,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Analyse cette image et produis le JSON.",
                        },
                    ],
                }
            ],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)

    except json.JSONDecodeError as exc:
        logger.warning("Claude vision a renvoyé un JSON invalide pour %s: %s", media_id, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Vision a échoué pour %s: %s", media_id, exc)
        return None
    finally:
        await client.close()

    row = {
        "media_id": media_id,
        "visual_description": result.get("visual_description"),
        "ocr_text": result.get("ocr_text"),
        "detected_objects": result.get("detected_objects") or [],
        "image_type": result.get("image_type"),
        "possible_anomaly": bool(result.get("possible_anomaly")),
        "anomaly_description": result.get("anomaly_description"),
        "confidence": float(result.get("confidence", 0.0)),
        "model_used": VISION_MODEL,
    }

    sb = get_supabase()
    sb.table("image_analysis").insert(row).execute()

    logger.info(
        "Vision analyzed media %s: type=%s, anomaly=%s, conf=%.2f",
        media_id, row["image_type"], row["possible_anomaly"], row["confidence"],
    )
    return row
