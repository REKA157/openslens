"""
Endpoints du module Exutoires (apports de déchets ultimes).

- GET  /api/exutoires?year=2026        : suivi contractuel vs réel + projection
- POST /admin/save-exutoires           : configure la liste des exutoires
- POST /admin/set-exutoire-real        : saisit un tonnage réel mensuel
- POST /admin/seed-exutoires-ads       : charge la config + le réel jan–mai ADS
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.db import get_supabase
from app.services.analytics import exutoires as exutoires_service

api_router = APIRouter(prefix="/api", tags=["exutoires"])
admin_router = APIRouter(prefix="/admin", tags=["exutoires-admin"])
logger = logging.getLogger(__name__)


def _check_admin(token: str | None) -> None:
    if settings.waha_webhook_secret and token != settings.waha_webhook_secret:
        raise HTTPException(status_code=401, detail="invalid admin token")


@api_router.get("/exutoires")
async def get_exutoires(year: int = Query(default=2026, ge=2020, le=2100)) -> dict[str, Any]:
    return exutoires_service.build_tracking(year)


# ---- Config ----------------------------------------------------------------

class ExutoireInput(BaseModel):
    canonical_name: str
    parent_group: str | None = None
    aliases: list[str] = Field(default_factory=list)
    contractual_annual_min: float = 0
    contractual_annual_max: float | None = None
    waste_filter: str | None = None
    is_active: bool = True


class SaveExutoiresRequest(BaseModel):
    exutoires: list[ExutoireInput]
    replace_all: bool = False


@admin_router.post("/save-exutoires")
async def save_exutoires(
    body: SaveExutoiresRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    _check_admin(x_admin_token)
    sb = get_supabase()
    stats = {"received": len(body.exutoires), "deleted_before": 0, "upserted": 0}
    if body.replace_all:
        deleted = sb.table("exutoires").delete().eq("company_id", settings.company_id).execute()
        stats["deleted_before"] = len(deleted.data or [])
    rows = [
        {
            "company_id": settings.company_id,
            "canonical_name": e.canonical_name.strip(),
            "parent_group": (e.parent_group or "").strip() or None,
            "aliases": [a.strip() for a in e.aliases if a and a.strip()],
            "contractual_annual_min": e.contractual_annual_min,
            "contractual_annual_max": e.contractual_annual_max,
            "waste_filter": (e.waste_filter or "").strip() or None,
            "is_active": e.is_active,
        }
        for e in body.exutoires if e.canonical_name.strip()
    ]
    if rows:
        res = sb.table("exutoires").upsert(rows, on_conflict="company_id,canonical_name").execute()
        stats["upserted"] = len(res.data or [])
    return stats


class SetRealRequest(BaseModel):
    canonical_name: str
    year: int = 2026
    month: int = Field(ge=1, le=12)
    tonnage_real: float


@admin_router.post("/set-exutoire-real")
async def set_exutoire_real(
    body: SetRealRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    _check_admin(x_admin_token)
    sb = get_supabase()
    ex = (
        sb.table("exutoires").select("id")
        .eq("company_id", settings.company_id)
        .eq("canonical_name", body.canonical_name.strip())
        .limit(1).execute()
    )
    if not ex.data:
        raise HTTPException(404, detail=f"Exutoire '{body.canonical_name}' introuvable")
    row = {
        "company_id": settings.company_id,
        "exutoire_id": ex.data[0]["id"],
        "year": body.year, "month": body.month,
        "tonnage_real": body.tonnage_real, "source": "manual",
    }
    sb.table("exutoire_monthly_real").upsert(
        row, on_conflict="company_id,exutoire_id,year,month"
    ).execute()
    return {"ok": True, **row}


# ---- Seed ADS (config + réel jan–mai 2026) ---------------------------------

_ADS_EXUTOIRES = [
    {"canonical_name": "SEMARDEL", "parent_group": None, "aliases": ["SEMARDEL"],
     "contractual_annual_min": 7000, "contractual_annual_max": 8000,
     "real": [395.12, 703.36, 414.44, 269.80, 41.60]},
    {"canonical_name": "SUEZ LIANCOURT", "parent_group": "SUEZ",
     "aliases": ["LIANCOURT", "SUEZ LIANCOURT"],
     "contractual_annual_min": 1600, "contractual_annual_max": 2200,
     "real": [313.34, 0.0, 185.20, 232.02, 208.36]},
    {"canonical_name": "SUEZ CAPOULADE PRUDEMANCHE", "parent_group": "SUEZ",
     "aliases": ["CAPOULADE", "PRUDEMANCHE", "SUEZ CAPOULADE"],
     "contractual_annual_min": 23000, "contractual_annual_max": 26000,
     "real": [1664.88, 2121.72, 2349.03, 2602.21, 1864.93]},
    {"canonical_name": "EMTA", "parent_group": None, "aliases": ["EMTA"],
     "contractual_annual_min": 10000, "contractual_annual_max": 10000,
     "real": [151.02, 445.66, 359.52, 253.52, 190.48]},
]


@admin_router.post("/seed-exutoires-ads")
async def seed_exutoires_ads(
    year: int = 2026,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Charge la config ADS + le réel jan–mai (depuis le tableau de suivi)."""
    _check_admin(x_admin_token)
    sb = get_supabase()
    seeded, real_rows = 0, 0
    for ex in _ADS_EXUTOIRES:
        up = sb.table("exutoires").upsert({
            "company_id": settings.company_id,
            "canonical_name": ex["canonical_name"],
            "parent_group": ex["parent_group"],
            "aliases": ex["aliases"],
            "contractual_annual_min": ex["contractual_annual_min"],
            "contractual_annual_max": ex["contractual_annual_max"],
            "is_active": True,
        }, on_conflict="company_id,canonical_name").execute()
        if not up.data:
            got = (
                sb.table("exutoires").select("id")
                .eq("company_id", settings.company_id)
                .eq("canonical_name", ex["canonical_name"]).limit(1).execute()
            )
            ex_id = got.data[0]["id"] if got.data else None
        else:
            ex_id = up.data[0]["id"]
        seeded += 1
        if ex_id:
            for i, val in enumerate(ex["real"], start=1):
                sb.table("exutoire_monthly_real").upsert({
                    "company_id": settings.company_id,
                    "exutoire_id": ex_id, "year": year, "month": i,
                    "tonnage_real": val, "source": "manual",
                }, on_conflict="company_id,exutoire_id,year,month").execute()
                real_rows += 1
    return {"seeded_exutoires": seeded, "real_rows": real_rows, "year": year}
