"""
Réconciliation WhatsApp ↔ MKGT.

Croise ce qui est ANNONCÉ sur WhatsApp (bons/factures détectés dans les
documents, table document_analysis) avec ce qui est ENREGISTRÉ dans MKGT
(table mkgt_operations).

Objectif métier : repérer les écarts —
  - wa_only   : un bon circule sur WhatsApp mais n'est pas dans MKGT
                → risque d'oubli de saisie / de facturation.
  - mkgt_only : une opération est dans MKGT sans trace WhatsApp
                → simple information (pas forcément un problème).
  - matched   : correspondance trouvée (par référence, ou site+date+tonnage).

Le rapprochement se fait d'abord par RÉFÉRENCE normalisée, puis en repli par
(site + date proche + tonnage proche).
"""

from __future__ import annotations

import logging
import re
from datetime import date as date_cls, timedelta
from typing import Any

from app.config import settings
from app.db import get_supabase
from app.services.analytics import quantitative

logger = logging.getLogger(__name__)

_FR_DATE_RE = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")


def _normalize_ref(ref: str | None) -> str:
    """Normalise une référence pour comparaison (chiffres+lettres, sans séparateurs)."""
    if not ref:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(ref).lower())


def _parse_any_date(values: Any) -> date_cls | None:
    """Extrait une date depuis un str, une liste de str (doc_dates), formats FR/ISO."""
    candidates: list[str] = []
    if isinstance(values, list):
        candidates = [str(v) for v in values if v]
    elif values:
        candidates = [str(values)]
    for c in candidates:
        c = c.strip()
        # ISO
        try:
            return date_cls.fromisoformat(c[:10])
        except ValueError:
            pass
        m = _FR_DATE_RE.search(c)
        if m:
            d, mo, y = m.groups()
            y = int(y) + (2000 if len(y) == 2 else 0)
            try:
                return date_cls(int(y), int(mo), int(d))
            except ValueError:
                continue
    return None


def _load_document_bons(period_start: date_cls, period_end: date_cls) -> list[dict]:
    """
    Charge les documents analysés (bons/factures/BSD) avec une date dans la
    fenêtre. La date vient de doc_dates (date portée sur le document), repli
    sur created_at.
    """
    sb = get_supabase()
    rows: list[dict] = []
    PAGE = 1000
    page = 0
    try:
        while page < 50:
            res = (
                sb.table("document_analysis")
                .select("id,media_id,document_type,reference,client_name,site_name,"
                        "waste_type,quantity,amount,doc_dates,created_at")
                .order("created_at", desc=True)
                .limit(PAGE)
                .offset(page * PAGE)
                .execute()
            )
            batch = res.data or []
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < PAGE:
                break
            page += 1
    except Exception as exc:  # noqa: BLE001 — table absente (migration v7 non jouée)
        logger.warning("_load_document_bons indisponible : %s", exc)
        return []

    out: list[dict] = []
    for r in rows:
        d = _parse_any_date(r.get("doc_dates")) or _parse_any_date(r.get("created_at"))
        if d and period_start <= d <= period_end:
            r["_date"] = d
            r["_tonnage"] = quantitative.parse_tonnage(r.get("quantity"))
            r["_amount"] = quantitative.parse_amount(r.get("amount"))
            out.append(r)
    return out


def reconcile_period(
    period_start: date_cls,
    period_end: date_cls,
    *,
    date_tolerance_days: int = 2,
    tonnage_tolerance_pct: float = 10.0,
) -> dict[str, Any]:
    """Rapproche documents WhatsApp et opérations MKGT sur la fenêtre."""
    bons = _load_document_bons(period_start, period_end)
    mkgt_ops = quantitative.load_mkgt_operations(period_start, period_end)

    # Index MKGT par référence normalisée
    mkgt_by_ref: dict[str, dict] = {}
    for op in mkgt_ops:
        nref = _normalize_ref(op.get("external_ref"))
        if nref:
            mkgt_by_ref.setdefault(nref, op)

    used_mkgt_ids: set[str] = set()
    matched: list[dict] = []
    wa_only: list[dict] = []

    def _op_date(op: dict) -> date_cls | None:
        try:
            return date_cls.fromisoformat(str(op.get("operation_date"))[:10])
        except (ValueError, TypeError):
            return None

    for bon in bons:
        nref = _normalize_ref(bon.get("reference"))
        match = None
        method = None

        # 1. Match par référence
        if nref and nref in mkgt_by_ref and mkgt_by_ref[nref]["id"] not in used_mkgt_ids:
            match = mkgt_by_ref[nref]
            method = "reference"

        # 2. Repli : site + date proche + tonnage proche
        if match is None:
            for op in mkgt_ops:
                if op["id"] in used_mkgt_ids:
                    continue
                od = _op_date(op)
                if od is None or abs((od - bon["_date"]).days) > date_tolerance_days:
                    continue
                # site
                if bon.get("site_name") and op.get("site_name"):
                    if _normalize_ref(bon["site_name"])[:6] not in _normalize_ref(op["site_name"]) \
                       and _normalize_ref(op["site_name"])[:6] not in _normalize_ref(bon["site_name"]):
                        continue
                # tonnage proche
                bt, ot = bon.get("_tonnage"), quantitative._to_tonnes(op.get("quantity"), op.get("unit"))
                if bt and ot:
                    if ot == 0 or abs(bt - ot) / max(ot, 0.001) * 100 > tonnage_tolerance_pct:
                        continue
                match = op
                method = "site_date_tonnage"
                break

        if match is not None:
            used_mkgt_ids.add(match["id"])
            matched.append({
                "reference": bon.get("reference"),
                "site_name": bon.get("site_name") or match.get("site_name"),
                "date": bon["_date"].isoformat(),
                "method": method,
                "wa_tonnage": bon.get("_tonnage"),
                "mkgt_tonnage": quantitative._to_tonnes(match.get("quantity"), match.get("unit")),
                "wa_amount": bon.get("_amount"),
                "mkgt_amount": match.get("amount_ht"),
            })
        else:
            wa_only.append({
                "reference": bon.get("reference"),
                "document_type": bon.get("document_type"),
                "site_name": bon.get("site_name"),
                "client_name": bon.get("client_name"),
                "date": bon["_date"].isoformat(),
                "tonnage": bon.get("_tonnage"),
                "amount": bon.get("_amount"),
            })

    mkgt_only = [
        {
            "external_ref": op.get("external_ref"),
            "site_name": op.get("site_name"),
            "client_name": op.get("client_name"),
            "operation_date": op.get("operation_date"),
            "tonnage": quantitative._to_tonnes(op.get("quantity"), op.get("unit")),
            "amount_ht": op.get("amount_ht"),
            "status": op.get("status"),
        }
        for op in mkgt_ops
        if op["id"] not in used_mkgt_ids
    ]

    return {
        "period": [period_start.isoformat(), period_end.isoformat()],
        "counts": {
            "wa_documents": len(bons),
            "mkgt_operations": len(mkgt_ops),
            "matched": len(matched),
            "wa_only": len(wa_only),
            "mkgt_only": len(mkgt_only),
        },
        "matched": matched,
        "wa_only": wa_only,        # annoncés sur WhatsApp, absents de MKGT
        "mkgt_only": mkgt_only,    # dans MKGT, sans trace WhatsApp
        "note": (
            "wa_only = bons vus sur WhatsApp mais pas dans MKGT (risque d'oubli "
            "de saisie/facturation). Vide tant que MKGT et/ou les documents ne "
            "sont pas alimentés."
        ),
    }
