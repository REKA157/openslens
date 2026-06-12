"""
Endpoints de l'analyse QUANTITATIVE (tonnages, montants, réconciliation).

- GET /api/quantitative      : totaux tonnage / € par site sur une période
- GET /api/forecast-tonnage  : prévision SARIMAX du tonnage par site (MKGT)
- GET /api/reconciliation    : rapprochement WhatsApp ↔ MKGT

Source principale : mkgt_operations. Tant que le CSV MKGT n'est pas importé,
ces endpoints renvoient des totaux à zéro + un message explicite.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from app.config import settings
from app.db import get_supabase
from app.services.ai.daily_report import compute_window
from app.services.analytics import quantitative, reconcile

router = APIRouter(prefix="/api", tags=["quantitative"])
logger = logging.getLogger(__name__)


def _load_sites() -> list[dict]:
    sb = get_supabase()
    res = (
        sb.table("sites")
        .select("id,canonical_name,aliases,region")
        .eq("company_id", settings.company_id)
        .eq("is_active", True)
        .execute()
    )
    return res.data or []


def _parse_date(date_str: str | None) -> date_cls:
    if not date_str:
        return datetime.now(tz=timezone.utc).date()
    try:
        return date_cls.fromisoformat(date_str[:10])
    except ValueError:
        return datetime.now(tz=timezone.utc).date()


@router.get("/quantitative")
async def quantitative_dashboard(
    date: str | None = Query(default=None),
    period: str = Query(default="month"),
    site_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Totaux tonnage / montant HT / nb opérations par site sur la période."""
    if period not in ("day", "week", "month"):
        period = "month"
    target = _parse_date(date)
    start, end = compute_window(target, period)  # type: ignore[arg-type]

    sites = _load_sites()
    mkgt_ops = quantitative.load_mkgt_operations(start, end, site_id=site_id)
    agg = quantitative.aggregate_by_site(mkgt_ops, sites)

    return {
        "period": period,
        "window": [start.isoformat(), end.isoformat()],
        "source": "mkgt_operations",
        "has_data": agg["totals"]["operations"] > 0,
        "empty_hint": (
            None if agg["totals"]["operations"] > 0
            else "Aucune donnée MKGT sur la période. Importe un CSV MKGT via /admin "
                 "pour activer le suivi en tonnes et en euros."
        ),
        **agg,
    }


@router.get("/forecast-tonnage")
async def forecast_tonnage(
    horizon_days: int = Query(default=30, ge=7, le=90),
    history_days: int = Query(default=180, ge=30, le=730),
    site_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """
    Prévision du TONNAGE par site (SARIMAX sur la série journalière MKGT).
    Repli moyenne mobile si l'historique est trop court pour SARIMAX.
    """
    today = datetime.now(tz=timezone.utc).date()
    from datetime import timedelta

    start = today - timedelta(days=history_days)
    sites = _load_sites()
    if site_id:
        sites = [s for s in sites if s["id"] == site_id]

    mkgt_ops = quantitative.load_mkgt_operations(start, today)

    results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for site in sites:
        series = quantitative.daily_tonnage_series(mkgt_ops, site["id"])
        if len(series) < 14:
            skipped.append({
                "site_id": site["id"],
                "site_name": site["canonical_name"],
                "reason": f"Historique tonnage insuffisant ({len(series)} jours).",
            })
            continue
        try:
            r = _forecast_one_series(series, horizon_days)
        except Exception as exc:  # noqa: BLE001
            logger.exception("forecast-tonnage échoué pour %s: %s", site["id"], exc)
            skipped.append({
                "site_id": site["id"], "site_name": site["canonical_name"],
                "reason": f"{type(exc).__name__}: {exc}",
            })
            continue
        results.append({
            "site_id": site["id"],
            "site_name": site["canonical_name"],
            "region": site.get("region"),
            **r,
        })

    results.sort(key=lambda x: x["summary"]["expected_total_tonnage"], reverse=True)
    return {
        "metric": "tonnage",
        "unit": "tonnes",
        "horizon_days": horizon_days,
        "ref_date": today.isoformat(),
        "modelled_count": len(results),
        "sites": results,
        "skipped": skipped,
        "has_data": len(results) > 0,
        "empty_hint": (
            None if results else
            "Pas assez de données MKGT pour prévoir des tonnages. Importe "
            "l'historique MKGT (CSV) pour activer."
        ),
    }


def _forecast_one_series(series: list[dict], horizon_days: int) -> dict[str, Any]:
    """Prévoit une série {ds,y} : SARIMAX si assez de points, sinon moyenne mobile."""
    import pandas as pd
    from datetime import timedelta

    df = pd.DataFrame(series)
    df["ds"] = pd.to_datetime(df["ds"])
    last_date = df["ds"].max().date()

    predictions: list[dict[str, Any]]
    method: str
    if len(df) >= 30:
        from app.services.predictive import prophet_forecaster
        fitted = prophet_forecaster.train_forecaster(df)
        raw = prophet_forecaster.predict_horizon(fitted, last_date, horizon_days=horizon_days)
        predictions = [
            {"date": p["date"], "yhat": p["yhat"],
             "yhat_lower": p["yhat_lower"], "yhat_upper": p["yhat_upper"]}
            for p in raw
        ]
        method = "sarimax"
    else:
        # Repli : moyenne mobile des 14 derniers jours
        recent = df.tail(14)["y"].mean()
        daily = round(float(recent), 3)
        predictions = [
            {"date": (last_date + timedelta(days=i + 1)).isoformat(),
             "yhat": daily, "yhat_lower": round(daily * 0.6, 3), "yhat_upper": round(daily * 1.4, 3)}
            for i in range(horizon_days)
        ]
        method = "moving_average"

    total = round(sum(p["yhat"] for p in predictions), 2)
    history_total = round(float(df["y"].sum()), 2)
    return {
        "method": method,
        "history": [
            {"date": row["ds"].date().isoformat(), "actual": round(float(row["y"]), 3)}
            for _, row in df.iterrows()
        ],
        "predictions": predictions,
        "summary": {
            "history_days": len(df),
            "history_total_tonnage": history_total,
            "expected_total_tonnage": total,
        },
    }


@router.get("/reconciliation")
async def reconciliation(
    date: str | None = Query(default=None),
    period: str = Query(default="month"),
) -> dict[str, Any]:
    """Rapprochement WhatsApp (documents) ↔ MKGT sur la période."""
    if period not in ("day", "week", "month"):
        period = "month"
    target = _parse_date(date)
    start, end = compute_window(target, period)  # type: ignore[arg-type]
    result = reconcile.reconcile_period(start, end)
    result["period_type"] = period
    return result
