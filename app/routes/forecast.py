"""
Endpoint /api/forecast — vraies prédictions de volume par site via Prophet.

Différent de /api/predictions (qui mélange descriptif + simple forecast) :
ici on fait du vrai modèle de séries temporelles avec saisonnalité multiple
et intégration des jours fériés FR.

Latence : ~5-15 sec par site lors du premier appel (training), <1 sec ensuite
(cache mémoire 1h).
"""

import logging
from datetime import date as date_cls, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.db import get_supabase
from app.routes.predictions import _load_corpus, _parse_ref_date
from app.services.predictive import prophet_forecaster

router = APIRouter(prefix="/api", tags=["forecast"])
logger = logging.getLogger(__name__)


@router.get("/forecast")
async def forecast(
    horizon_days: int = Query(
        default=30, ge=7, le=60, description="Nombre de jours à prédire",
    ),
    site_id: str | None = Query(
        default=None, description="Optionnel : un site précis. Sinon : tous les sites éligibles.",
    ),
) -> dict[str, Any]:
    sites, messages, classifications_by_id = _load_corpus()

    if not sites:
        return {
            "horizon_days": horizon_days,
            "sites": [],
            "warning": "Aucun site canonique défini. Va sur /sites pour les paramétrer.",
        }

    if site_id:
        sites = [s for s in sites if s["id"] == site_id]
        if not sites:
            raise HTTPException(404, detail="Site introuvable")

    results: list[dict[str, Any]] = []
    for site in sites:
        try:
            r = prophet_forecaster.forecast_site(
                site, messages, classifications_by_id,
                horizon_days=horizon_days,
                history_tail_days=90,
            )
            if r is not None:
                results.append(r)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Forecast Prophet échoué pour %s : %s", site.get("canonical_name"), exc,
            )

    for m in messages:
        m.pop("_parsed_ts", None)

    results.sort(key=lambda r: r["summary"]["expected_total"], reverse=True)

    return {
        "horizon_days": horizon_days,
        "ref_date": datetime.now(tz=timezone.utc).date().isoformat(),
        "sites_count": len(sites),
        "modelled_count": len(results),
        "messages_scanned": len(messages),
        "sites": results,
    }
