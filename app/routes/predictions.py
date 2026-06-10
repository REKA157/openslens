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
from app.services.analytics import insights as insights_service
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


def _parse_ref_date(date: str | None) -> date_cls:
    if date:
        try:
            return date_cls.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, detail="date doit être YYYY-MM-DD") from None
    return datetime.now(tz=timezone.utc).date()


def _load_corpus() -> tuple[list[dict], list[dict], dict[str, dict]]:
    """
    Charge sites actifs + tous les messages + classifications correspondantes.
    Pré-parse les timestamps sur les messages (champ _parsed_ts).
    Renvoie (sites, messages, classifications_by_id).
    """
    sb = get_supabase()

    sites_res = (
        sb.table("sites")
        .select("*")
        .eq("company_id", settings.company_id)
        .eq("is_active", True)
        .execute()
    )
    sites = sites_res.data or []

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

    for m in messages:
        m["_parsed_ts"] = _parse_dt(m.get("sent_at"))

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

    return sites, messages, classifications_by_id


def _compute_signals(
    sites: list[dict],
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    ref_date: date_cls,
) -> dict[str, Any]:
    return {
        "anomalies": predictive.detect_anomalies(
            messages, classifications_by_id, sites, ref_date,
        ),
        "trends": predictive.compute_trends(
            messages, classifications_by_id, sites, ref_date,
        ),
        "forecast": predictive.forecast_demand(
            messages, classifications_by_id, sites, ref_date,
        ),
        "recurring_failures": predictive.detect_recurring_failures(
            messages, classifications_by_id, sites, ref_date,
        ),
    }


@router.get("/predictions")
async def predictions(
    date: str | None = Query(default=None, description="YYYY-MM-DD ; défaut = aujourd'hui UTC"),
) -> dict[str, Any]:
    ref_date = _parse_ref_date(date)
    sites, messages, classifications_by_id = _load_corpus()

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

    signals = _compute_signals(sites, messages, classifications_by_id, ref_date)

    for m in messages:
        m.pop("_parsed_ts", None)

    return {
        "ref_date": ref_date.isoformat(),
        "sites_count": len(sites),
        "messages_scanned": len(messages),
        "classifications_loaded": len(classifications_by_id),
        **signals,
    }


@router.post("/predictions/insights")
async def predictions_insights(
    date: str | None = Query(default=None, description="YYYY-MM-DD ; défaut = aujourd'hui UTC"),
) -> dict[str, Any]:
    """
    Croisement quantitatif × qualitatif via Claude Sonnet.

    Étapes :
     1. Calcule les 4 signaux statistiques (anomalies/trends/forecast/failures)
     2. Récupère un échantillon des 15 derniers messages classifiés par site
     3. Appelle Claude Sonnet → alertes priorisées + actions recommandées + croisements

    Latence : 15-30 s. Coût : ~0,05-0,10 $ par appel.
    """
    ref_date = _parse_ref_date(date)
    sites, messages, classifications_by_id = _load_corpus()

    if not sites:
        raise HTTPException(
            400,
            detail="Aucun site canonique défini. Va sur /sites pour les paramétrer avant les insights IA.",
        )

    signals = _compute_signals(sites, messages, classifications_by_id, ref_date)
    contextual = insights_service.gather_context_per_site(
        messages, classifications_by_id, sites, ref_date,
    )

    try:
        ai_insights = await insights_service.generate_insights(
            signals, sites, contextual,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Insights IA échoués : %s", exc)
        raise HTTPException(500, detail=f"Échec génération insights : {exc}")

    for m in messages:
        m.pop("_parsed_ts", None)

    return {
        "ref_date": ref_date.isoformat(),
        "sites_count": len(sites),
        "messages_scanned": len(messages),
        "classifications_loaded": len(classifications_by_id),
        **signals,
        "insights": ai_insights,
    }
