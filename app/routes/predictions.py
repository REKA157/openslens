"""
Endpoint /api/predictions — agrège les 4 signaux prédictifs OpsLens.

Charge en mémoire (paginé) :
 - tous les sites canoniques actifs
 - tous les messages
 - toutes les classifications

Puis calcule via app.services.analytics.predictive :
 - anomalies (Z-score volume/urgences semaine courante)
 - tendances (delta % sur 4 vs 4 semaines glissantes)
 - forecast hebdo (saisonnalité jour-semaine)
 - pannes récurrentes (vehicles mentionnés ≥ N fois dans des incidents)

Latence attendue : 2-5 sec avec ~5000 messages + ~3000 classifs.
"""

import logging
from datetime import date as date_cls, datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.db import get_supabase
from app.services.analytics import predictive

router = APIRouter(prefix="/api", tags=["predictions"])
logger = logging.getLogger(__name__)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


@router.get("/predictions")
async def predictions(
    date: str | None = Query(default=None, description="YYYY-MM-DD ; défaut = aujourd'hui UTC"),
) -> dict[str, Any]:
    if date:
        try:
            ref_date = date_cls.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, detail="date doit être YYYY-MM-DD") from None
    else:
        ref_date = datetime.now(tz=timezone.utc).date()

    sb = get_supabase()

    # 1. Sites actifs
    sites_res = (
        sb.table("sites")
        .select("*")
        .eq("company_id", settings.company_id)
        .eq("is_active", True)
        .execute()
    )
    sites = sites_res.data or []

    if not sites:
        return {
            "ref_date": ref_date.isoformat(),
            "sites_count": 0,
            "messages_scanned": 0,
            "classifications_loaded": 0,
            "anomalies": [],
            "trends": [],
            "forecast": [],
            "recurring_failures": [],
            "warning": "Aucun site canonique défini. Va sur /sites pour les paramétrer.",
        }

    # 2. Messages (paginé par 1000)
    messages: list[dict] = []
    PAGE = 1000
    page = 0
    while page < 20:
        res = (
            sb.table("whatsapp_messages")
            .select("id,sent_at,sender_display_name,raw_text")
            .order("sent_at", desc=False)
            .limit(PAGE)
            .offset(page * PAGE)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        messages.extend(rows)
        if len(rows) < PAGE:
            break
        page += 1

    # Pré-parser les timestamps une fois pour tous
    for m in messages:
        m["_parsed_ts"] = _parse_dt(m.get("sent_at"))

    # 3. Classifications (par chunks de 100 ids)
    msg_ids = [m["id"] for m in messages]
    classifications: list[dict] = []
    for i in range(0, len(msg_ids), 100):
        chunk = msg_ids[i : i + 100]
        res = (
            sb.table("message_classifications")
            .select("*")
            .in_("message_id", chunk)
            .execute()
        )
        classifications.extend(res.data or [])
    classifications_by_id = {c["message_id"]: c for c in classifications}

    # 4. Calcul des 4 signaux
    anomalies = predictive.detect_anomalies(
        messages, classifications_by_id, sites, ref_date
    )
    trends = predictive.compute_trends(
        messages, classifications_by_id, sites, ref_date
    )
    forecast = predictive.forecast_demand(
        messages, classifications_by_id, sites, ref_date
    )
    failures = predictive.detect_recurring_failures(
        messages, classifications_by_id, sites, ref_date
    )

    # Nettoyage : retire le champ technique avant le retour
    for m in messages:
        m.pop("_parsed_ts", None)

    return {
        "ref_date": ref_date.isoformat(),
        "sites_count": len(sites),
        "messages_scanned": len(messages),
        "classifications_loaded": len(classifications),
        "anomalies": anomalies,
        "trends": trends,
        "forecast": forecast,
        "recurring_failures": failures,
    }
