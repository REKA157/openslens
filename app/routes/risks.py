"""
Endpoints d'analyse des risques opérationnels (Phase 2 OpsLens).

Sprint 1 : /api/risks/overflow-incidents
  Inventaire historique des incidents de débordement à partir des messages
  WhatsApp. Sert à évaluer le volume de données labellisées disponibles
  avant de décider du modèle (ML vs règles).
"""

import logging
from typing import Any

from fastapi import APIRouter, Query

from app.routes.predictions import _load_corpus
from app.services.risks import overflow_incidents

router = APIRouter(prefix="/api/risks", tags=["risks"])
logger = logging.getLogger(__name__)


@router.get("/overflow-incidents")
async def list_overflow_incidents(
    include_weak: bool = Query(
        default=True,
        description="Inclure les signaux faibles (mentions peu spécifiques). Désactive pour ne voir que les vrais incidents.",
    ),
    site_id: str | None = Query(
        default=None,
        description="Filtrer sur un site précis.",
    ),
) -> dict[str, Any]:
    sites, messages, classifications_by_id = _load_corpus()

    result = overflow_incidents.extract_overflow_incidents(
        messages, classifications_by_id, sites,
    )

    # Nettoyage : retire le champ technique
    for m in messages:
        m.pop("_parsed_ts", None)

    # Filtrage post-extraction
    incidents = result["incidents"]
    if not include_weak:
        incidents = [i for i in incidents if i["level"] != "weak"]
    if site_id:
        incidents = [i for i in incidents if i["site_id"] == site_id]

    return {
        "summary": result["summary"],
        "data_quality": result["data_quality"],
        "filters_applied": {
            "include_weak": include_weak,
            "site_id": site_id,
        },
        "incidents_returned": len(incidents),
        "incidents": incidents,
    }
