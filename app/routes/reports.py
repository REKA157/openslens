"""
Endpoints pour les rapports narratifs (jour / semaine / mois).

Nouveaux endpoints (v17) :
- POST /admin/generate-report           : body {period, target_date, force}
- GET  /api/reports                     : ?period=&date=  → rapport de cette période
- GET  /api/reports/list                : ?period=        → liste des rapports
- GET  /api/reports/latest              : ?period=        → dernier rapport

Endpoints v15 (compat /daily/*) :
- POST /admin/generate-daily-report     : ancien alias, force period=day
- GET  /api/reports/daily/latest        : dernier rapport JOUR
- GET  /api/reports/daily               : liste des rapports JOUR uniquement
- GET  /api/reports/daily/{date}        : rapport JOUR à cette date

NB : `report_date` en DB = period_start (lundi pour week, 1er pour month).
"""

import logging
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from app.config import settings
from app.db import get_supabase
from app.services.ai import daily_report as report_service

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["admin"])
api_router = APIRouter(prefix="/api/reports", tags=["reports"])


PeriodType = Literal["day", "week", "month"]


class GenerateReportRequest(BaseModel):
    period: PeriodType = "day"
    target_date: str | None = None  # YYYY-MM-DD, défaut = aujourd'hui UTC
    force: bool = False


# --- Helpers ----------------------------------------------------------------


def _parse_date(value: str | None) -> date:
    if not value:
        return datetime.now(tz=timezone.utc).date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date attendue au format YYYY-MM-DD") from None


def _check_admin(token: str | None) -> None:
    if settings.waha_webhook_secret and token != settings.waha_webhook_secret:
        raise HTTPException(status_code=401, detail="invalid admin token")


# --- Endpoints v17 unifiés --------------------------------------------------


@admin_router.post("/generate-report")
async def generate_report_endpoint(
    body: GenerateReportRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    _check_admin(x_admin_token)
    target = _parse_date(body.target_date)
    try:
        return await report_service.generate_report(target, body.period, force=body.force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Génération rapport %s échouée: %s", body.period, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@api_router.get("")
async def get_report(
    period: PeriodType = Query("day"),
    date: str | None = Query(None, description="YYYY-MM-DD ; défaut = aujourd'hui UTC"),
):
    """
    Retourne le rapport correspondant à la période contenant `date`.
    On normalise `date` vers period_start (lundi / 1er du mois) pour la lookup.
    """
    target = _parse_date(date)
    period_start, _ = report_service.compute_window(target, period)

    sb = get_supabase()
    res = (
        sb.table("daily_reports")
        .select("*")
        .eq("company_id", settings.company_id)
        .eq("period_type", period)
        .eq("report_date", period_start.isoformat())
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Pas de rapport pour cette période")
    return res.data[0]


@api_router.get("/list")
async def list_reports(period: PeriodType = Query("day")):
    """Liste des rapports d'une période donnée (max 60)."""
    sb = get_supabase()
    res = (
        sb.table("daily_reports")
        .select("report_date,period_end,created_at,stats")
        .eq("company_id", settings.company_id)
        .eq("period_type", period)
        .order("report_date", desc=True)
        .limit(60)
        .execute()
    )
    return {"period": period, "reports": res.data or []}


@api_router.get("/latest")
async def latest_report(period: PeriodType = Query("day")):
    sb = get_supabase()
    res = (
        sb.table("daily_reports")
        .select("*")
        .eq("company_id", settings.company_id)
        .eq("period_type", period)
        .order("report_date", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Aucun rapport disponible")
    return res.data[0]


# --- Endpoints v15 (rétrocompat /daily/*) -----------------------------------


class GenerateDailyReportRequest(BaseModel):
    target_date: str | None = None
    force: bool = False


@admin_router.post("/generate-daily-report")
async def generate_daily_report_legacy(
    body: GenerateDailyReportRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Alias historique — équivalent à period='day'."""
    _check_admin(x_admin_token)
    target = _parse_date(body.target_date)
    try:
        return await report_service.generate_report(target, "day", force=body.force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Génération rapport quotidien échouée: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@api_router.get("/daily/latest")
async def latest_daily_report_legacy():
    sb = get_supabase()
    res = (
        sb.table("daily_reports")
        .select("*")
        .eq("company_id", settings.company_id)
        .eq("period_type", "day")
        .order("report_date", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Aucun rapport disponible")
    return res.data[0]


@api_router.get("/daily/{report_date}")
async def daily_report_by_date_legacy(report_date: str):
    target = _parse_date(report_date)
    sb = get_supabase()
    res = (
        sb.table("daily_reports")
        .select("*")
        .eq("company_id", settings.company_id)
        .eq("period_type", "day")
        .eq("report_date", target.isoformat())
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Pas de rapport pour cette date")
    return res.data[0]


@api_router.get("/daily")
async def list_daily_reports_legacy():
    sb = get_supabase()
    res = (
        sb.table("daily_reports")
        .select("report_date,created_at")
        .eq("company_id", settings.company_id)
        .eq("period_type", "day")
        .order("report_date", desc=True)
        .limit(30)
        .execute()
    )
    return {"reports": res.data or []}
