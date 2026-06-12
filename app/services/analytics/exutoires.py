"""
Suivi des apports de déchets ultimes par EXUTOIRE.

Pour chaque exutoire (SEMARDEL, SUEZ Liancourt, SUEZ Capoulade, EMTA…) :
  - objectif contractuel annuel (min/max), proraté au mois par jours travaillés
  - tonnage réel mensuel : depuis mkgt_operations si alimenté, sinon depuis la
    saisie manuelle (exutoire_monthly_real)
  - % atteinte, delta, cumul, et projection fin d'année à rythme constant

Calque la logique du tableau Excel ADS (contractuel pondéré jours ouvrés).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as date_cls
from typing import Any

from app.config import settings
from app.db import get_supabase
from app.services.analytics import quantitative

logger = logging.getLogger(__name__)

# Jours travaillés par mois (source : tableau ADS). Total 252 pour 2026.
WORKING_DAYS: dict[int, list[int]] = {
    2026: [21, 20, 22, 21, 17, 22, 22, 21, 22, 22, 20, 22],
}
MONTH_LABELS = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sep", "Oct", "Nov", "Déc"]


def _working_days(year: int) -> list[int]:
    if year in WORKING_DAYS:
        return WORKING_DAYS[year]
    # Repli : répartition uniforme ~252/12
    return [21] * 12


def load_exutoires() -> list[dict]:
    sb = get_supabase()
    try:
        res = (
            sb.table("exutoires")
            .select("id,canonical_name,parent_group,aliases,contractual_annual_min,"
                    "contractual_annual_max,waste_filter,is_active")
            .eq("company_id", settings.company_id)
            .eq("is_active", True)
            .order("contractual_annual_min", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_exutoires indisponible (migration v8 ?) : %s", exc)
        return []


def _load_monthly_real(year: int) -> dict[str, dict[int, float]]:
    """{exutoire_id: {month: tonnage}} depuis la saisie manuelle."""
    sb = get_supabase()
    out: dict[str, dict[int, float]] = defaultdict(dict)
    try:
        res = (
            sb.table("exutoire_monthly_real")
            .select("exutoire_id,month,tonnage_real")
            .eq("company_id", settings.company_id)
            .eq("year", year)
            .execute()
        )
        for r in res.data or []:
            out[r["exutoire_id"]][int(r["month"])] = float(r["tonnage_real"] or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_load_monthly_real indisponible : %s", exc)
    return out


def _match_exutoire(ex: dict, op: dict) -> bool:
    """Vrai si un alias de l'exutoire apparaît dans les champs de l'opération MKGT."""
    aliases = [a.lower() for a in (ex.get("aliases") or []) if a]
    aliases.append(ex["canonical_name"].lower())
    haystack_parts = [
        str(op.get("client_name") or ""),
        str(op.get("site_name") or ""),
        str(op.get("waste_type") or ""),
    ]
    raw = op.get("raw_data")
    if isinstance(raw, dict):
        haystack_parts.extend(str(v) for v in raw.values())
    hay = " | ".join(haystack_parts).lower()
    return any(a and a in hay for a in aliases)


def _mkgt_real_by_exutoire(year: int, exutoires: list[dict]) -> dict[str, dict[int, float]]:
    """{exutoire_id: {month: tonnage}} calculé depuis mkgt_operations."""
    ops = quantitative.load_mkgt_operations(date_cls(year, 1, 1), date_cls(year, 12, 31))
    out: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for op in ops:
        d_str = op.get("operation_date")
        if not d_str:
            continue
        try:
            month = int(str(d_str)[5:7])
        except (ValueError, IndexError):
            continue
        tonnes = quantitative._to_tonnes(op.get("quantity"), op.get("unit"))
        if not tonnes:
            continue
        for ex in exutoires:
            wf = (ex.get("waste_filter") or "").lower()
            if wf and wf not in str(op.get("waste_type") or "").lower():
                continue
            if _match_exutoire(ex, op):
                out[ex["id"]][month] += tonnes
                break
    return out


def build_tracking(year: int) -> dict[str, Any]:
    """Construit le suivi complet contractuel vs réel + projection par exutoire."""
    exutoires = load_exutoires()
    wd = _working_days(year)
    total_wd = sum(wd)

    mkgt_real = _mkgt_real_by_exutoire(year, exutoires)
    manual_real = _load_monthly_real(year)

    per_exutoire: list[dict] = []
    source_used = "none"

    for ex in exutoires:
        ex_id = ex["id"]
        annual_min = float(ex.get("contractual_annual_min") or 0)
        annual_max = ex.get("contractual_annual_max")

        # Source du réel : MKGT en priorité si l'exutoire y a des données
        mk = mkgt_real.get(ex_id) or {}
        mk_total = sum(mk.values())
        if mk_total > 0:
            real_by_month = {m: round(v, 2) for m, v in mk.items()}
            src = "mkgt"
        else:
            real_by_month = {m: round(v, 2) for m, v in (manual_real.get(ex_id) or {}).items()}
            src = "manual" if real_by_month else "none"
        if src != "none" and source_used != "mkgt":
            source_used = src

        months = []
        cumul_real = 0.0
        cumul_contract = 0.0
        last_month_with_data = 0
        for m in range(1, 13):
            contract_m = round(annual_min * wd[m - 1] / total_wd, 2) if total_wd else 0
            real_m = real_by_month.get(m)
            has = real_m is not None
            if has:
                last_month_with_data = m
                cumul_real += real_m
            cumul_contract += contract_m
            months.append({
                "month": m, "label": MONTH_LABELS[m - 1],
                "contractual": contract_m,
                "real": real_m,
                "pct": round(real_m / contract_m * 100, 1) if (has and contract_m) else None,
            })

        # Projection fin d'année à rythme constant sur jours ouvrés écoulés
        elapsed_wd = sum(wd[:last_month_with_data]) if last_month_with_data else 0
        projection = round(cumul_real / elapsed_wd * total_wd, 0) if elapsed_wd else 0
        pct_annual = round(cumul_real / annual_min * 100, 1) if annual_min else None
        pct_proj = round(projection / annual_min * 100, 1) if annual_min else None
        delta_proj = round(projection - annual_min, 0)

        status = "ok"
        if pct_proj is not None:
            if pct_proj < 70:
                status = "critique"
            elif pct_proj < 95:
                status = "sous_objectif"
            elif annual_max and projection > float(annual_max):
                status = "sur_objectif"

        per_exutoire.append({
            "id": ex_id,
            "name": ex["canonical_name"],
            "parent_group": ex.get("parent_group"),
            "contractual_annual_min": annual_min,
            "contractual_annual_max": float(annual_max) if annual_max is not None else None,
            "real_source": src,
            "cumul_real": round(cumul_real, 2),
            "cumul_contractual_period": round(cumul_contract if last_month_with_data == 0 else sum(
                round(annual_min * wd[m - 1] / total_wd, 2) for m in range(1, last_month_with_data + 1)
            ), 2),
            "pct_annual": pct_annual,
            "projection_annual": projection,
            "pct_projection": pct_proj,
            "delta_projection": delta_proj,
            "status": status,
            "months": months,
        })

    totals = {
        "contractual_annual": round(sum(e["contractual_annual_min"] for e in per_exutoire), 2),
        "cumul_real": round(sum(e["cumul_real"] for e in per_exutoire), 2),
        "projection_annual": round(sum(e["projection_annual"] for e in per_exutoire), 2),
    }
    totals["pct_annual"] = (
        round(totals["cumul_real"] / totals["contractual_annual"] * 100, 1)
        if totals["contractual_annual"] else None
    )
    totals["delta_projection"] = round(totals["projection_annual"] - totals["contractual_annual"], 0)

    return {
        "year": year,
        "working_days_total": total_wd,
        "source_used": source_used,
        "has_config": len(exutoires) > 0,
        "has_data": totals["cumul_real"] > 0,
        "empty_hint": (
            None if exutoires else
            "Aucun exutoire configuré. Lance POST /admin/seed-exutoires-ads pour "
            "charger la configuration ADS (SEMARDEL, SUEZ, EMTA)."
        ),
        "totals": totals,
        "exutoires": per_exutoire,
    }
