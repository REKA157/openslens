"""
Classification métier d'un message via Claude.

Produit un JSON structuré conforme au schéma OpsLens :
  - business_category, priority, summary, entities, action_required, etc.

Stockage du résultat : table `message_classifications`.

Stratégie coût/qualité :
- Modèle par défaut : Claude Haiku 4.5 (rapide, peu cher, suffisant pour 90% des cas)
- Prompt système caché pour économiser sur les appels répétés
"""

import json
import logging
from typing import Any

import anthropic

from app.config import settings
from app.db import get_supabase

logger = logging.getLogger(__name__)


# Catégories métier autorisées (cf. spec OpsLens)
CATEGORIES = [
    "info",
    "demande_action",
    "validation",
    "refus",
    "incident",
    "urgence",
    "retard",
    "panne",
    "reclamation_client",
    "probleme_documentaire",
    "facturation",
    "livraison",
    "intervention",
    "maintenance",
    "qualite",
    "securite",
    "rh_operationnel",
    "conflit_operationnel",
    "preuve_photo",
    "document_recu",
    "document_manquant",
    "relance",
    "decision",
    "instruction",
    "cloture_action",
    "non_exploitable",
]

PRIORITIES = ["low", "medium", "high", "urgent"]
RISK_LEVELS = ["none", "low", "medium", "high"]


SYSTEM_PROMPT = f"""Tu es un analyseur d'opérations terrain pour une entreprise française \
de collecte et évacuation de déchets (PVC, ferraille, alu, bennes grutables) opérant en \
Île-de-France. Le métier inclut : chauffeurs, sites clients, bons de livraison, contrôles \
techniques, interventions urgentes, gestion de bennes.

Ton rôle : transformer un message WhatsApp pro en JSON structuré exploitable pour \
l'analyse opérationnelle.

Règles strictes :
1. Tu réponds UNIQUEMENT par un objet JSON conforme au schéma fourni. Aucun texte avant/après.
2. Si une information est absente, mets null — n'invente jamais.
3. `confidence` : 0.0 si tu doutes fortement, 1.0 si certain. Sois honnête.
4. Tu ne portes JAMAIS de jugement personnel sur les employés.
5. `summary` : 1 phrase factuelle, en français, neutre, < 200 caractères.
6. Langues attendues : français, arabe, darija marocaine, anglais, mixte. Identifie.

Catégories autorisées (`business_category`) : {", ".join(CATEGORIES)}
Priorités autorisées (`priority`) : {", ".join(PRIORITIES)}
Niveaux de risque (`risk_level`) : {", ".join(RISK_LEVELS)}

Schéma JSON à produire EXACTEMENT :
{{
  "business_category": "<une des catégories>",
  "priority": "<une des priorités>",
  "language": "<fr | ar | darija | en | mixed | unknown>",
  "summary": "<phrase factuelle française>",
  "entities": {{
    "clients": ["<nom client>"],
    "sites": ["<nom site>"],
    "vehicles": ["<plaque ou nom>"],
    "employees": ["<nom>"],
    "documents": ["<type doc>"],
    "amounts": ["<montant>"],
    "dates": ["<date mentionnée>"]
  }},
  "action_required": true/false,
  "action_description": "<description action ou null>",
  "deadline": "<ISO date ou null>",
  "risk_level": "<niveau>",
  "operational_impact": "<phrase courte ou null>",
  "confidence": 0.0-1.0
}}"""


async def classify_message(message_uuid: str, enriched_text: str) -> dict | None:
    """
    Classifie un message via Claude et stocke le résultat dans
    message_classifications. Retourne le dict ou None si erreur.
    """
    if not settings.anthropic_api_key:
        logger.warning("ANTHROPIC_API_KEY non configurée, skip classification")
        return None

    if not enriched_text or len(enriched_text.strip()) < 2:
        logger.debug("Message vide ou trop court, skip classification")
        return None

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    try:
        response = await client.messages.create(
            model=settings.classification_model,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # économie 90% sur prompt
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"Message à analyser :\n\n{enriched_text}\n\nProduis le JSON.",
                }
            ],
        )

        raw = response.content[0].text.strip()
        # Nettoie d'éventuels markdown fences
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)

    except json.JSONDecodeError as exc:
        logger.warning("Claude a renvoyé un JSON invalide pour %s: %s", message_uuid, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Classification a échoué pour %s: %s", message_uuid, exc)
        return None
    finally:
        await client.close()

    # Validation minimale
    confidence = float(result.get("confidence", 0.0))
    requires_review = confidence < 0.7 or result.get("priority") == "urgent"

    row = {
        "message_id": message_uuid,
        "business_category": result.get("business_category"),
        "priority": result.get("priority"),
        "language": result.get("language"),
        "summary": result.get("summary"),
        "entities": result.get("entities") or {},
        "action_required": bool(result.get("action_required")),
        "action_description": result.get("action_description"),
        "deadline": result.get("deadline"),
        "risk_level": result.get("risk_level"),
        "operational_impact": result.get("operational_impact"),
        "requires_human_review": requires_review,
        "confidence": confidence,
        "model_used": settings.classification_model,
    }

    sb = get_supabase()
    sb.table("message_classifications").insert(row).execute()

    logger.info(
        "Classified message %s as %s (priority=%s, confidence=%.2f)",
        message_uuid, row["business_category"], row["priority"], confidence,
    )
    return row
