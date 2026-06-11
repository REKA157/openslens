"""
Endpoint /api/process/analysis — 4 indicateurs de processus opérationnel ADS.

Retourne les 4 analyses en un seul appel :
  - response_times    : délais demande→résolution
  - repeated_requests : demandes répétées (process cassé)
  - dead_threads      : action_required sans résolution > 48h
  - critical_hours    : heatmap 7×24 des urgences

Pure agrégation, pas d'appel LLM. ~3-5 sec pour ~5000 messages.
"""

import logging
from typing import Any

from fastapi import APIRouter, Query

from app.routes.predictions import _load_corpus
from app.services.analytics import process_analysis

router = APIRouter(prefix="/api/process", tags=["process"])
logger = logging.getLogger(__name__)


@router.get("/analysis")
async def analyze_process(
    response_window_hours: int = Query(72, ge=12, le=168),
    repeated_window_days: int = Query(7, ge=2, le=30),
    dead_min_age_hours: int = Query(48, ge=12, le=336),
    critical_site_id: str | None = Query(None),
) -> dict[str, Any]:
    sites, messages, classifications_by_id = _load_corpus()

    response_times = process_analysis.analyze_response_times(
        messages, classifications_by_id, sites,
        window_hours=response_window_hours,
    )
    repeated = process_analysis.detect_repeated_requests(
        messages, classifications_by_id, sites,
        window_days=repeated_window_days,
    )
    dead = process_analysis.detect_dead_threads(
        messages, classifications_by_id, sites,
        min_age_hours=dead_min_age_hours,
    )
    critical = process_analysis.compute_critical_hours(
        messages, classifications_by_id, sites,
        site_id=critical_site_id,
    )

    for m in messages:
        m.pop("_parsed_ts", None)

    return {
        "messages_scanned": len(messages),
        "classifications_loaded": len(classifications_by_id),
        "sites_count": len(sites),
        "response_times": response_times,
        "repeated_requests": repeated,
        "dead_threads": dead,
        "critical_hours": critical,
    }
