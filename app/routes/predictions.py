"""
Endpoint /api/predictions — agrège les 4 signaux prédictifs OpsLens
+ diagnostic prédictif Claude Sonnet en mode async (start + poll).

Le mode async est nécessaire parce que :
- L'appel Claude Sonnet prend 15-40s
- Le calcul des signaux ajoute 5-10s
- Total ~30-60s, juste à la limite du cap 60s de Vercel Hobby

Workflow async :
  1. POST /api/predictions/insights/start
     → crée un job_id, lance le calcul en background, retourne immédiatement
  2. GET /api/predictions/insights/status?job_id=X
     → polls l'état (running / done / failed)
  3. Le frontend appelle (2) toutes les 3 sec jusqu'à done.

Stockage des jobs : in-memory dict côté FastAPI. Suffisant pour le pilote
(perdu si Coolify reboot — l'utilisateur relance, c'est rapide).
"""

import asyncio
import logging
import time
import uuid
from datetime import date as date_cls, datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from app.config import settings
from app.db import get_supabase
from app.services.analytics import insights as insights_service
from app.services.analytics import predictive

router = APIRouter(prefix="/api", tags=["predictions"])
logger = logging.getLogger(__name__)


# --- Job store in-memory pour le diagnostic async --------------------------

_JOBS: dict[str, dict[str, Any]] = {}
_JOB_TTL_SECONDS = 60 * 30  # purge les jobs terminés depuis > 30 min


def _purge_old_jobs() -> None:
    """Nettoie les jobs anciens pour limiter la conso mémoire."""
    now = time.time()
    expired = [
        jid for jid, j in _JOBS.items()
        if j.get("status") in ("done", "failed")
        and now - j.get("finished_at", now) > _JOB_TTL_SECONDS
    ]
    for jid in expired:
        _JOBS.pop(jid, None)


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
        logger.exception("Diagnostic prédictif échoué : %s", exc)
        raise HTTPException(500, detail=f"Échec du diagnostic prédictif : {exc}")

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


# --- Mode async : start + poll ---------------------------------------------


async def _run_insights_job(job_id: str, date: str | None) -> None:
    """
    Worker exécuté en background task : compute signaux + appel Claude Sonnet,
    stocke le résultat dans _JOBS[job_id].
    """
    job = _JOBS[job_id]
    job["status"] = "running"
    try:
        ref_date = _parse_ref_date(date)
        sites, messages, classifications_by_id = _load_corpus()

        if not sites:
            raise ValueError(
                "Aucun site canonique défini. Va sur /sites pour les paramétrer.",
            )

        signals = _compute_signals(
            sites, messages, classifications_by_id, ref_date,
        )
        contextual = insights_service.gather_context_per_site(
            messages, classifications_by_id, sites, ref_date,
        )
        ai_insights = await insights_service.generate_insights(
            signals, sites, contextual,
        )

        for m in messages:
            m.pop("_parsed_ts", None)

        job["result"] = {
            "ref_date": ref_date.isoformat(),
            "sites_count": len(sites),
            "messages_scanned": len(messages),
            "classifications_loaded": len(classifications_by_id),
            **signals,
            "insights": ai_insights,
        }
        job["status"] = "done"
        job["finished_at"] = time.time()
        logger.info(
            "Job %s terminé (durée %.1fs)",
            job_id, job["finished_at"] - job["started_at"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s a échoué : %s", job_id, exc)
        job["status"] = "failed"
        job["error"] = str(exc)
        job["finished_at"] = time.time()


@router.post("/predictions/insights/start")
async def start_insights_job(
    background_tasks: BackgroundTasks,
    date: str | None = Query(default=None, description="YYYY-MM-DD"),
) -> dict[str, Any]:
    """
    Démarre un job de diagnostic prédictif en arrière-plan.
    Retourne immédiatement un job_id à poller via /status.

    Le calcul tourne dans une background task ; la requête HTTP elle-même
    rend la main en <100 ms, ce qui contourne les caps de timeout des
    proxies (Vercel Hobby = 60s).
    """
    _purge_old_jobs()
    job_id = uuid.uuid4().hex[:16]
    _JOBS[job_id] = {
        "id": job_id,
        "status": "pending",
        "started_at": time.time(),
        "date_param": date,
    }
    # asyncio task plutôt que background_tasks pour qu'elle survive au retour
    asyncio.create_task(_run_insights_job(job_id, date))
    return {"job_id": job_id, "status": "running"}


@router.get("/predictions/insights/status")
async def get_insights_status(
    job_id: str = Query(..., description="ID du job retourné par /start"),
) -> dict[str, Any]:
    """
    Renvoie l'état d'un job : pending / running / done / failed.
    Si done : `result` contient le payload complet.
    Si failed : `error` contient le détail.
    """
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail="Job introuvable ou expiré")

    elapsed = time.time() - job["started_at"]
    out: dict[str, Any] = {
        "job_id": job_id,
        "status": job["status"],
        "elapsed_seconds": round(elapsed, 1),
    }
    if job["status"] == "done":
        out["result"] = job["result"]
    elif job["status"] == "failed":
        out["error"] = job.get("error", "unknown")
    return out
