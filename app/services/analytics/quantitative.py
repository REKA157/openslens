"""
Couche d'analyse QUANTITATIVE d'OpsLens.

Jusqu'ici tout l'analytique comptait des MESSAGES. Ici on exploite les vraies
grandeurs métier : tonnages et montants.

Sources :
  - mkgt_operations  : source de vérité (quantity NUMERIC, amount_ht, date, site)
  - document_analysis: secondaire — quantités/montants extraits des bons/factures
                       WhatsApp (texte → nombre), surtout utile en réconciliation.

Fonctions :
  - parse_tonnage / parse_amount : "12,5 T" / "1 250 €" → float
  - load_mkgt_operations(...)     : opérations MKGT d'une fenêtre
  - aggregate_by_site(...)        : totaux tonnage / montant / nb ops par site
  - daily_tonnage_series(...)     : série journalière (pour la prévision)
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date as date_cls
from typing import Any

from app.config import settings
from app.db import get_supabase

logger = logging.getLogger(__name__)


# --- Parsing nombres FR ------------------------------------------------------


def _parse_number(value: str | None) -> float | None:
    """Parse un nombre au format FR (virgule décimale, espaces milliers)."""
    if value is None:
        return None
    v = re.sub(r"[\s ']", "", str(value).strip())
    v = v.replace(",", ".").replace("€", "").replace("EUR", "").replace("$", "")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", v)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def parse_tonnage(text: str | None) -> float | None:
    """
    Convertit un texte de quantité en TONNES.
    "12,5 T" -> 12.5 ; "12500 kg" -> 12.5 ; "5 m3" -> None (volume, pas masse).
    """
    if not text:
        return None
    t = str(text).lower()
    # Volume seul → on n'agrège pas avec la masse
    if ("m3" in t or "m³" in t) and "t" not in re.sub(r"m3|m³", "", t):
        return None
    num = _parse_number(t)
    if num is None:
        return None
    if "kg" in t:
        return round(num / 1000.0, 3)
    return num  # défaut : tonnes


def parse_amount(text: str | None) -> float | None:
    """Convertit un texte de montant en float (euros)."""
    return _parse_number(text)


# --- Chargement MKGT ---------------------------------------------------------


def load_mkgt_operations(
    period_start: date_cls,
    period_end: date_cls,
    site_id: str | None = None,
) -> list[dict]:
    """Charge les opérations MKGT dont operation_date est dans [start, end]."""
    sb = get_supabase()
    rows: list[dict] = []
    PAGE = 1000
    page = 0
    try:
        while page < 50:
            q = (
                sb.table("mkgt_operations")
                .select("id,external_ref,operation_date,client_name,site_name,site_id,"
                        "waste_type,container_type,quantity,unit,status,amount_ht")
                .eq("company_id", settings.company_id)
                .gte("operation_date", period_start.isoformat())
                .lte("operation_date", period_end.isoformat())
                .order("operation_date", desc=False)
                .limit(PAGE)
                .offset(page * PAGE)
            )
            if site_id:
                q = q.eq("site_id", site_id)
            res = q.execute()
            batch = res.data or []
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < PAGE:
                break
            page += 1
    except Exception as exc:  # noqa: BLE001 — table absente (migration v6 non jouée) ou autre
        logger.warning("load_mkgt_operations indisponible (table MKGT ?) : %s", exc)
        return []
    return rows


def _is_mass_unit(unit: str | None) -> bool:
    if not unit:
        return True  # défaut : on suppose des tonnes
    u = unit.strip().lower()
    return u in {"t", "tonne", "tonnes", "to", "kg"} or "tonne" in u


def _to_tonnes(quantity: float | None, unit: str | None) -> float | None:
    if quantity is None:
        return None
    if unit and unit.strip().lower() == "kg":
        return quantity / 1000.0
    if _is_mass_unit(unit):
        return quantity
    return None  # m³ ou autre → pas une masse


# --- Agrégation --------------------------------------------------------------


def aggregate_by_site(
    mkgt_ops: list[dict],
    sites: list[dict],
) -> dict[str, Any]:
    """
    Totaux par site canonique : tonnage, montant HT, nb opérations, top matières.
    Rattachement : mkgt.site_id si présent, sinon match du site_name aux aliases.
    """
    from app.routes.sites import site_alias_match

    site_by_id = {s["id"]: s for s in sites}

    per_site: dict[str, dict[str, Any]] = {}
    unmatched_tonnage = 0.0
    unmatched_amount = 0.0
    unmatched_count = 0

    def _bucket(site_id: str | None, name: str | None) -> dict[str, Any] | None:
        if site_id and site_id in site_by_id:
            key = site_id
            disp = site_by_id[site_id]["canonical_name"]
        elif name:
            matched = None
            for s in sites:
                aliases = (s.get("aliases") or []) + [s["canonical_name"]]
                if site_alias_match([name], aliases):
                    matched = s
                    break
            if matched:
                key, disp = matched["id"], matched["canonical_name"]
            else:
                return None
        else:
            return None
        if key not in per_site:
            per_site[key] = {
                "site_id": key, "site_name": disp,
                "tonnage": 0.0, "amount_ht": 0.0, "operations": 0,
                "by_waste_type": defaultdict(float),
            }
        return per_site[key]

    for op in mkgt_ops:
        tonnes = _to_tonnes(op.get("quantity"), op.get("unit"))
        amount = op.get("amount_ht") or 0.0
        bucket = _bucket(op.get("site_id"), op.get("site_name"))
        if bucket is None:
            unmatched_count += 1
            unmatched_tonnage += tonnes or 0.0
            unmatched_amount += float(amount or 0.0)
            continue
        bucket["operations"] += 1
        if tonnes:
            bucket["tonnage"] += tonnes
        if amount:
            bucket["amount_ht"] += float(amount)
        if op.get("waste_type") and tonnes:
            bucket["by_waste_type"][op["waste_type"]] += tonnes

    sites_out = []
    for b in per_site.values():
        b["tonnage"] = round(b["tonnage"], 2)
        b["amount_ht"] = round(b["amount_ht"], 2)
        b["by_waste_type"] = [
            {"waste_type": k, "tonnage": round(v, 2)}
            for k, v in sorted(b["by_waste_type"].items(), key=lambda x: x[1], reverse=True)
        ]
        sites_out.append(b)
    sites_out.sort(key=lambda x: x["tonnage"], reverse=True)

    totals = {
        "tonnage": round(sum(s["tonnage"] for s in sites_out) + unmatched_tonnage, 2),
        "amount_ht": round(sum(s["amount_ht"] for s in sites_out) + unmatched_amount, 2),
        "operations": sum(s["operations"] for s in sites_out) + unmatched_count,
        "tonnage_matched_sites": round(sum(s["tonnage"] for s in sites_out), 2),
    }

    return {
        "by_site": sites_out,
        "totals": totals,
        "unmatched": {
            "operations": unmatched_count,
            "tonnage": round(unmatched_tonnage, 2),
            "amount_ht": round(unmatched_amount, 2),
        },
    }


def daily_tonnage_series(mkgt_ops: list[dict], site_id: str) -> list[dict]:
    """
    Série journalière de tonnage pour un site (pour la prévision SARIMAX).
    Retourne [{ds: date iso, y: tonnage}] continue (jours vides à 0).
    """
    from datetime import timedelta

    daily: dict[date_cls, float] = defaultdict(float)
    for op in mkgt_ops:
        if op.get("site_id") != site_id:
            continue
        d_str = op.get("operation_date")
        if not d_str:
            continue
        try:
            d = date_cls.fromisoformat(str(d_str)[:10])
        except ValueError:
            continue
        tonnes = _to_tonnes(op.get("quantity"), op.get("unit"))
        if tonnes:
            daily[d] += tonnes

    if not daily:
        return []
    out = []
    cur, last = min(daily), max(daily)
    while cur <= last:
        out.append({"ds": cur.isoformat(), "y": round(daily.get(cur, 0.0), 3)})
        cur += timedelta(days=1)
    return out
