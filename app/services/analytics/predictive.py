"""
Signaux prédictifs opérationnels pour OpsLens.

4 fonctions, toutes basées sur l'agrégation des classifications IA existantes
(pas d'appel LLM). Pure stats descriptive :

 1. detect_anomalies(...)         : Z-score volume + urgences semaine courante
 2. compute_trends(...)            : delta % sur 4 vs 4 semaines glissantes
 3. forecast_demand(...)           : moyenne mobile + saisonnalité jour-semaine
 4. detect_recurring_failures(...) : vehicles mentionnés dans plusieurs incidents

Toutes prennent les mêmes inputs préchargés en mémoire (évite N+1 queries) :
  - messages         : rows whatsapp_messages, chacune avec un `_parsed_ts`
  - classifications_by_id : {message_id: classification_row}
  - sites            : rows sites canoniques (canonical_name + aliases)
  - ref_date         : date de référence (= aujourd'hui en général)
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Any

from app.routes.sites import site_alias_match

logger = logging.getLogger(__name__)


# --- Helpers ----------------------------------------------------------------


def _week_start_date(d: date_cls) -> date_cls:
    """Lundi de la semaine ISO contenant `d`."""
    return d - timedelta(days=d.weekday())


def _classify_messages_by_site(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
) -> dict[str, list[dict]]:
    """
    Pour chaque site, liste les messages dont la classification mentionne
    un de ses aliases. Un message peut tomber dans plusieurs sites (rare).
    """
    by_site: dict[str, list[dict]] = defaultdict(list)
    for m in messages:
        c = classifications_by_id.get(m["id"])
        if not c:
            continue
        ents_sites = (c.get("entities") or {}).get("sites") or []
        if not ents_sites:
            continue
        for s in sites:
            aliases = s.get("aliases") or []
            if site_alias_match(ents_sites, aliases):
                by_site[s["id"]].append(m)
    return by_site


def _msg_date(m: dict) -> date_cls | None:
    ts: datetime | None = m.get("_parsed_ts")
    if ts is None:
        return None
    return ts.astimezone(timezone.utc).date()


# --- 1. Détection d'anomalies -----------------------------------------------


def detect_anomalies(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
    ref_date: date_cls,
    *,
    weeks_history: int = 12,
    z_threshold: float = 2.0,
) -> list[dict[str, Any]]:
    """
    Pour chaque site, Z-score du volume + des urgents sur la semaine courante
    vs les `weeks_history` semaines précédentes. Retourne les sites avec
    |Z| >= threshold (ou avec Z urgents >= threshold).
    """
    ref_week_start = _week_start_date(ref_date)
    by_site = _classify_messages_by_site(messages, classifications_by_id, sites)

    anomalies: list[dict[str, Any]] = []
    for site in sites:
        site_msgs = by_site.get(site["id"], [])
        if len(site_msgs) < 20:  # base trop faible pour parler de Z-score
            continue

        weekly_vol: Counter = Counter()
        weekly_urg: Counter = Counter()
        for m in site_msgs:
            d = _msg_date(m)
            if d is None:
                continue
            wk = _week_start_date(d)
            weekly_vol[wk] += 1
            c = classifications_by_id.get(m["id"]) or {}
            if c.get("priority") == "urgent":
                weekly_urg[wk] += 1

        history_weeks = sorted(w for w in weekly_vol if w < ref_week_start)[-weeks_history:]
        if len(history_weeks) < 4:
            continue

        history_vol = [weekly_vol[w] for w in history_weeks]
        history_urg = [weekly_urg[w] for w in history_weeks]
        current_vol = weekly_vol.get(ref_week_start, 0)
        current_urg = weekly_urg.get(ref_week_start, 0)

        def _z(curr: float, hist: list[float]) -> float | None:
            if len(hist) < 2:
                return None
            mu = statistics.mean(hist)
            sigma = statistics.pstdev(hist)
            if sigma < 0.5:
                return None
            return (curr - mu) / sigma

        z_vol = _z(current_vol, history_vol)
        z_urg = _z(current_urg, history_urg)

        if z_vol is None and z_urg is None:
            continue
        z_max = max(abs(z_vol or 0), abs(z_urg or 0))
        if z_max < z_threshold:
            continue

        anomalies.append({
            "site_id": site["id"],
            "site_name": site["canonical_name"],
            "region": site.get("region"),
            "current_week_start": ref_week_start.isoformat(),
            "volume": {
                "current": current_vol,
                "mean_history": round(statistics.mean(history_vol), 1),
                "z_score": round(z_vol, 2) if z_vol is not None else None,
            },
            "urgent": {
                "current": current_urg,
                "mean_history": round(statistics.mean(history_urg), 1),
                "z_score": round(z_urg, 2) if z_urg is not None else None,
            },
            "severity": "high" if z_max >= 3 else "medium",
            "history_weeks_used": len(history_weeks),
        })

    anomalies.sort(
        key=lambda a: max(
            abs(a["volume"]["z_score"] or 0),
            abs(a["urgent"]["z_score"] or 0),
        ),
        reverse=True,
    )
    return anomalies


# --- 2. Tendances longues ---------------------------------------------------


_TREND_CATEGORIES = (
    "incident",
    "urgence",
    "panne",
    "livraison",
    "demande_action",
    "intervention",
    "maintenance",
)


def compute_trends(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
    ref_date: date_cls,
    *,
    window_days: int = 28,
    min_pct_delta: float = 25.0,
) -> list[dict[str, Any]]:
    """
    Pour chaque site, delta % du volume et des catégories clés sur les
    `window_days` derniers jours vs la fenêtre précédente.
    Garde les sites avec au moins une variation >= `min_pct_delta` %.
    """
    by_site = _classify_messages_by_site(messages, classifications_by_id, sites)

    recent_start = ref_date - timedelta(days=window_days)
    prev_start = recent_start - timedelta(days=window_days)

    def _bucket(msgs: list[dict], start: date_cls, end: date_cls) -> list[dict]:
        out = []
        for m in msgs:
            d = _msg_date(m)
            if d and start <= d < end:
                out.append(m)
        return out

    def _delta_pct(curr: int, prev: int) -> float:
        if prev == 0:
            return 100.0 if curr > 0 else 0.0
        return round((curr - prev) / prev * 100, 1)

    trends: list[dict[str, Any]] = []
    for site in sites:
        site_msgs = by_site.get(site["id"], [])
        recent = _bucket(site_msgs, recent_start, ref_date)
        prev = _bucket(site_msgs, prev_start, recent_start)
        if len(recent) + len(prev) < 10:
            continue

        site_trend: dict[str, Any] = {
            "site_id": site["id"],
            "site_name": site["canonical_name"],
            "region": site.get("region"),
            "window_recent": [recent_start.isoformat(), ref_date.isoformat()],
            "window_prev": [prev_start.isoformat(), recent_start.isoformat()],
            "volume": {
                "recent": len(recent),
                "prev": len(prev),
                "delta_pct": _delta_pct(len(recent), len(prev)),
            },
            "by_category": {},
        }

        notable = abs(site_trend["volume"]["delta_pct"]) >= min_pct_delta
        for cat in _TREND_CATEGORIES:
            r = sum(
                1 for m in recent
                if (classifications_by_id.get(m["id"]) or {}).get("business_category") == cat
            )
            p = sum(
                1 for m in prev
                if (classifications_by_id.get(m["id"]) or {}).get("business_category") == cat
            )
            if r + p < 3:
                continue
            delta = _delta_pct(r, p)
            site_trend["by_category"][cat] = {
                "recent": r,
                "prev": p,
                "delta_pct": delta,
            }
            if abs(delta) >= min_pct_delta:
                notable = True

        if notable:
            trends.append(site_trend)

    trends.sort(key=lambda t: abs(t["volume"]["delta_pct"]), reverse=True)
    return trends


# --- 3. Forecast hebdomadaire -----------------------------------------------


_DAY_LABELS = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")


def forecast_demand(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
    ref_date: date_cls,
    *,
    weeks_history: int = 12,
) -> list[dict[str, Any]]:
    """
    Pour chaque site, prédit le volume attendu de la semaine prochaine
    en se basant sur le pattern jour-semaine des N dernières semaines.
    """
    ref_week_start = _week_start_date(ref_date)
    history_start = ref_week_start - timedelta(weeks=weeks_history)
    by_site = _classify_messages_by_site(messages, classifications_by_id, sites)

    forecasts: list[dict[str, Any]] = []
    for site in sites:
        site_msgs = by_site.get(site["id"], [])
        if len(site_msgs) < 30:
            continue

        weekly_dow: dict[date_cls, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for m in site_msgs:
            d = _msg_date(m)
            if d is None or d < history_start or d >= ref_week_start:
                continue
            weekly_dow[_week_start_date(d)][d.weekday()] += 1

        weeks = sorted(weekly_dow.keys())
        if len(weeks) < 4:
            continue

        per_dow_stats: dict[int, dict[str, float]] = {}
        for dow in range(7):
            counts = [weekly_dow[w].get(dow, 0) for w in weeks]
            mu = statistics.mean(counts)
            sigma = statistics.pstdev(counts) if len(counts) > 1 else 0.0
            per_dow_stats[dow] = {"mean": round(mu, 1), "stdev": round(sigma, 1)}

        next_week_start = ref_week_start + timedelta(days=7)
        expected_total = sum(s["mean"] for s in per_dow_stats.values())
        confidence_band = sum(s["stdev"] for s in per_dow_stats.values())

        forecasts.append({
            "site_id": site["id"],
            "site_name": site["canonical_name"],
            "region": site.get("region"),
            "next_week_start": next_week_start.isoformat(),
            "expected_total": round(expected_total, 1),
            "confidence_band": round(confidence_band, 1),
            "by_day": [
                {
                    "day": _DAY_LABELS[dow],
                    "expected": per_dow_stats[dow]["mean"],
                    "stdev": per_dow_stats[dow]["stdev"],
                }
                for dow in range(7)
            ],
            "history_weeks": len(weeks),
        })

    forecasts.sort(key=lambda f: f["expected_total"], reverse=True)
    return forecasts


# --- 4. Pannes récurrentes --------------------------------------------------


_FAILURE_CATEGORIES = {"panne", "incident", "maintenance"}


def detect_recurring_failures(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
    ref_date: date_cls,
    *,
    months_history: int = 3,
    min_occurrences: int = 3,
) -> list[dict[str, Any]]:
    """
    Pour chaque (site, vehicle), compte les messages catégorisés
    panne/incident/maintenance dans les N derniers mois. Retourne les
    couples avec >= min_occurrences.
    """
    cutoff = ref_date - timedelta(days=months_history * 30)
    by_site = _classify_messages_by_site(messages, classifications_by_id, sites)
    site_name_by_id = {s["id"]: s["canonical_name"] for s in sites}

    failures: list[dict[str, Any]] = []
    for site_id, site_msgs in by_site.items():
        vehicle_count: Counter = Counter()
        vehicle_examples: dict[str, list[dict]] = defaultdict(list)
        for m in site_msgs:
            d = _msg_date(m)
            if d is None or d < cutoff:
                continue
            c = classifications_by_id.get(m["id"]) or {}
            if c.get("business_category") not in _FAILURE_CATEGORIES:
                continue
            for v in (c.get("entities") or {}).get("vehicles") or []:
                if not isinstance(v, str):
                    continue
                v_clean = v.strip()
                if not v_clean:
                    continue
                vehicle_count[v_clean] += 1
                if len(vehicle_examples[v_clean]) < 3:
                    vehicle_examples[v_clean].append({
                        "date": d.isoformat(),
                        "summary": (c.get("summary") or (m.get("raw_text") or "")[:140]),
                        "priority": c.get("priority"),
                    })

        for vehicle, count in vehicle_count.items():
            if count < min_occurrences:
                continue
            failures.append({
                "site_id": site_id,
                "site_name": site_name_by_id.get(site_id, "?"),
                "vehicle": vehicle,
                "incidents_count": count,
                "examples": vehicle_examples[vehicle],
            })

    failures.sort(key=lambda f: f["incidents_count"], reverse=True)
    return failures
