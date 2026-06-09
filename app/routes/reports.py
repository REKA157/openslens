"""
Endpoints pour les rapports quotidiens.

- POST /admin/generate-daily-report : génère (ou regénère) un rapport
- GET  /api/reports/daily/latest    : retourne le dernier rapport généré
- GET  /api/reports/daily/{date}    : retourne le rapport d'une date (YYYY-MM-DD)
"""

import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.db import get_supabase
from app.services.ai import daily_report as report_service

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["admin"])
api_router = APIRouter(prefix="/api/reports", tags=["reports"])


class GenerateReportRequest(BaseModel):
    target_date: str | None = None  # YYYY-MM-DD, défaut = aujourd'hui UTC
    force: bool = False


@admin_router.post("/generate-daily-report")
async def generate_daily_report(
    body: GenerateReportRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    if body.target_date:
        try:
            target = date.fromisoformat(body.target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="target_date doit être YYYY-MM-DD")
    else:
        target = datetime.now(tz=timezone.utc).date()

    try:
        report = await report_service.generate_daily_report(target, force=body.force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Génération rapport échouée: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return report


@api_router.get("/daily/latest")
async def latest_daily_report():
    sb = get_supabase()
    res = (
        sb.table("daily_reports")
        .select("*")
        .eq("company_id", settings.company_id)
        .order("report_date", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Aucun rapport disponible")
    return res.data[0]


@api_router.get("/daily/{report_date}")
async def daily_report_by_date(report_date: str):
    try:
        target = date.fromisoformat(report_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date doit être YYYY-MM-DD")

    sb = get_supabase()
    res = (
        sb.table("daily_reports")
        .select("*")
        .eq("company_id", settings.company_id)
        .eq("report_date", target.isoformat())
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Pas de rapport pour cette date")
    return res.data[0]


@api_router.get("/daily")
async def list_daily_reports():
    """Liste les rapports disponibles (date uniquement, pour menu de sélection)."""
    sb = get_supabase()
    res = (
        sb.table("daily_reports")
        .select("report_date,created_at")
        .eq("company_id", settings.company_id)
        .order("report_date", desc=True)
        .limit(30)
        .execute()
    )
    return {"reports": res.data or []}
