"""
Analyse des processus opérationnels ADS à partir des messages WhatsApp.

4 indicateurs :
  1. Délais demande → résolution (response_times)
  2. Demandes répétées dans fenêtre 7-14 j (repeated_requests)
  3. Fils morts : action_required sans résolution > 48h (dead_threads)
  4. Heures critiques : heatmap 7×24 des urgences (critical_hours)

Pure agrégation, pas d'IA ici. Utilise les classifications IA existantes
(business_category, priority, action_required, entities) comme entrée.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Any

from app.routes.sites import site_alias_match


# Catégories qui marquent une résolution
_RESOLUTION_CATEGORIES = {"cloture_action", "validation", "decision"}

# Mots-clés textuels de résolution (en plus de la catégorie)
_RESOLUTION_PATTERNS = [
    re.compile(r"\bfait\b", re.IGNORECASE),
    re.compile(r"\bregle[e]?\b", re.IGNORECASE),
    re.compile(r"\blivre[e]?\b", re.IGNORECASE),
    re.compile(r"\bok\s+merci\b", re.IGNORECASE),
    re.compile(r"\bok\s+(?:bien\s+)?(?:re[cç]u|note)", re.IGNORECASE),
    re.compile(r"\b(?:bien\s+)?re[cç]u\b", re.IGNORECASE),
    re.compile(r"\bcollecte\s+(?:effectu|faite)", re.IGNORECASE),
    re.compile(r"\bevacue", re.IGNORECASE),
    re.compile(r"\benleve[e]?\b", re.IGNORECASE),
    re.compile(r"\bvide[e]?\b", re.IGNORECASE),
    re.compile(r"\bresolu", re.IGNORECASE),
    re.compile(r"\btraite[e]?\b", re.IGNORECASE),
]


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _site_of_message(
    classification: dict | None,
    sites: list[dict],
) -> tuple[str | None, str | None]:
    if not classification:
        return None, None
    ents_sites = (classification.get("entities") or {}).get("sites") or []
    if not ents_sites:
        return None, None
    for s in sites:
        aliases = s.get("aliases") or []
        if site_alias_match(ents_sites, aliases):
            return s["id"], s["canonical_name"]
    return None, None


def _is_resolution(msg: dict, classification: dict | None) -> bool:
    """Heuristique : ce message ressemble-t-il à une résolution / clôture ?"""
    if classification:
        cat = classification.get("business_category")
        if cat in _RESOLUTION_CATEGORIES:
            return True
    text = (msg.get("raw_text") or "").lower()
    if not text:
        return False
    for pattern in _RESOLUTION_PATTERNS:
        if pattern.search(text):
            return True
    return False


# ============================================================================
# 1. Délais demande → résolution
# ============================================================================


def analyze_response_times(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
    *,
    window_hours: int = 72,
) -> dict[str, Any]:
    """
    Pour chaque message avec action_required=true, cherche la première
    résolution potentielle dans les `window_hours` qui suivent, sur le
    même site. Calcule les délais.
    """
    # Indexe les résolutions par site et timestamp
    resolutions_by_site: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
    requests: list[dict] = []

    for m in messages:
        ts = m.get("_parsed_ts")
        if ts is None:
            continue
        c = classifications_by_id.get(m["id"])
        if not c:
            continue
        site_id, site_name = _site_of_message(c, sites)
        if site_id is None:
            continue

        if _is_resolution(m, c):
            resolutions_by_site[site_id].append((ts, m))

        if c.get("action_required"):
            requests.append({
                "msg": m,
                "ts": ts,
                "classification": c,
                "site_id": site_id,
                "site_name": site_name,
            })

    # Pour chaque request, cherche la première résolution dans la fenêtre
    delays_by_site: dict[str, list[float]] = defaultdict(list)
    delays_by_category: dict[str, list[float]] = defaultdict(list)
    site_names: dict[str, str] = {}
    matched: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for req in requests:
        site_id = req["site_id"]
        site_names[site_id] = req["site_name"]
        req_ts = req["ts"]
        deadline = req_ts + timedelta(hours=window_hours)

        candidates = [
            (r_ts, r_msg)
            for r_ts, r_msg in resolutions_by_site.get(site_id, [])
            if r_ts > req_ts and r_ts <= deadline
        ]
        if not candidates:
            unresolved.append({
                "message_id": req["msg"]["id"],
                "site_name": req["site_name"],
                "sent_at": req_ts.isoformat(),
                "category": (req["classification"] or {}).get("business_category"),
                "priority": (req["classification"] or {}).get("priority"),
                "summary": (req["classification"] or {}).get("summary"),
            })
            continue

        candidates.sort(key=lambda x: x[0])
        resolution_ts, _ = candidates[0]
        delay_h = (resolution_ts - req_ts).total_seconds() / 3600.0
        delays_by_site[site_id].append(delay_h)
        cat = (req["classification"] or {}).get("business_category") or "?"
        delays_by_category[cat].append(delay_h)
        matched.append({
            "message_id": req["msg"]["id"],
            "site_name": req["site_name"],
            "category": cat,
            "delay_hours": round(delay_h, 1),
        })

    def _stats(values: list[float]) -> dict[str, float]:
        if not values:
            return {"n": 0}
        sorted_v = sorted(values)
        n = len(sorted_v)
        return {
            "n": n,
            "median": round(statistics.median(sorted_v), 1),
            "p90": round(sorted_v[int(0.9 * (n - 1))], 1) if n > 1 else round(sorted_v[0], 1),
            "mean": round(statistics.mean(sorted_v), 1),
            "max": round(max(sorted_v), 1),
        }

    by_site_stats = []
    for sid, vals in delays_by_site.items():
        s = _stats(vals)
        by_site_stats.append({
            "site_id": sid,
            "site_name": site_names.get(sid, "?"),
            **s,
        })
    by_site_stats.sort(key=lambda x: x.get("median", 0), reverse=True)

    by_category_stats = [
        {"category": cat, **_stats(vals)}
        for cat, vals in delays_by_category.items()
    ]
    by_category_stats.sort(key=lambda x: x.get("median", 0), reverse=True)

    return {
        "window_hours": window_hours,
        "total_requests": len(requests),
        "matched_resolutions": len(matched),
        "unresolved_count": len(unresolved),
        "global_stats": _stats([d for vals in delays_by_site.values() for d in vals]),
        "by_site": by_site_stats,
        "by_category": by_category_stats,
    }


# ============================================================================
# 2. Demandes répétées
# ============================================================================


def detect_repeated_requests(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
    *,
    window_days: int = 7,
    min_repetitions: int = 2,
) -> dict[str, Any]:
    """
    Heuristique : on groupe les action_required par (site, catégorie). Si
    le même couple revient plus de N fois dans `window_days`, c'est suspect.
    """
    # Construit la liste de demandes
    requests: list[dict] = []
    for m in messages:
        ts = m.get("_parsed_ts")
        c = classifications_by_id.get(m["id"])
        if ts is None or not c or not c.get("action_required"):
            continue
        site_id, site_name = _site_of_message(c, sites)
        if site_id is None:
            continue
        requests.append({
            "msg": m,
            "ts": ts,
            "site_id": site_id,
            "site_name": site_name,
            "category": c.get("business_category"),
            "summary": c.get("summary") or (m.get("raw_text") or "")[:150],
        })

    requests.sort(key=lambda r: r["ts"])

    # Glissement fenêtre : pour chaque (site, cat), liste des timestamps
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in requests:
        key = (r["site_id"], r["category"] or "?")
        by_key[key].append(r)

    clusters: list[dict[str, Any]] = []
    for (site_id, cat), reqs in by_key.items():
        if len(reqs) < min_repetitions:
            continue
        # Fenêtre glissante : cherche les groupes >= min_repetitions sur window_days
        window = timedelta(days=window_days)
        used: set[str] = set()
        for i, anchor in enumerate(reqs):
            if anchor["msg"]["id"] in used:
                continue
            group = [anchor]
            for j in range(i + 1, len(reqs)):
                if reqs[j]["ts"] - anchor["ts"] <= window:
                    group.append(reqs[j])
                else:
                    break
            if len(group) >= min_repetitions:
                for g in group:
                    used.add(g["msg"]["id"])
                clusters.append({
                    "site_name": anchor["site_name"],
                    "site_id": site_id,
                    "category": cat,
                    "count": len(group),
                    "window_start": group[0]["ts"].isoformat(),
                    "window_end": group[-1]["ts"].isoformat(),
                    "span_hours": round(
                        (group[-1]["ts"] - group[0]["ts"]).total_seconds() / 3600.0,
                        1,
                    ),
                    "examples": [
                        {
                            "date": g["ts"].strftime("%Y-%m-%d %H:%M"),
                            "summary": g["summary"],
                        }
                        for g in group[:5]
                    ],
                })

    clusters.sort(key=lambda c: c["count"], reverse=True)

    # Stats agrégées
    by_site_counter: Counter[str] = Counter()
    for c in clusters:
        by_site_counter[c["site_name"]] += c["count"]

    return {
        "window_days": window_days,
        "min_repetitions": min_repetitions,
        "clusters_count": len(clusters),
        "total_requests_in_clusters": sum(c["count"] for c in clusters),
        "by_site_summary": [
            {"site_name": n, "repeated_requests": k}
            for n, k in by_site_counter.most_common()
        ],
        "clusters": clusters[:50],
    }


# ============================================================================
# 3. Fils morts (action_required > 48h sans résolution)
# ============================================================================


def detect_dead_threads(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
    *,
    min_age_hours: int = 48,
    window_hours: int = 168,  # 7 j max
) -> dict[str, Any]:
    """
    Identifie les demandes anciennes (> min_age_hours) qui n'ont reçu aucune
    résolution dans une fenêtre de `window_hours`. Probable oubli.
    """
    now = datetime.now(tz=timezone.utc)

    resolutions_by_site: dict[str, list[datetime]] = defaultdict(list)
    requests: list[dict] = []

    for m in messages:
        ts = m.get("_parsed_ts")
        if ts is None:
            continue
        c = classifications_by_id.get(m["id"])
        if not c:
            continue
        site_id, site_name = _site_of_message(c, sites)
        if site_id is None:
            continue

        if _is_resolution(m, c):
            resolutions_by_site[site_id].append(ts)

        if c.get("action_required"):
            requests.append({
                "msg": m,
                "ts": ts,
                "site_id": site_id,
                "site_name": site_name,
                "classification": c,
            })

    dead: list[dict[str, Any]] = []
    for req in requests:
        age_hours = (now - req["ts"]).total_seconds() / 3600.0
        if age_hours < min_age_hours:
            continue  # encore récent

        deadline = req["ts"] + timedelta(hours=window_hours)
        has_resolution = any(
            req["ts"] < r_ts <= deadline
            for r_ts in resolutions_by_site.get(req["site_id"], [])
        )
        if has_resolution:
            continue

        dead.append({
            "message_id": req["msg"]["id"],
            "sent_at": req["ts"].isoformat(),
            "age_days": round(age_hours / 24, 1),
            "site_id": req["site_id"],
            "site_name": req["site_name"],
            "category": req["classification"].get("business_category"),
            "priority": req["classification"].get("priority"),
            "summary": req["classification"].get("summary")
                or (req["msg"].get("raw_text") or "")[:200],
            "sender": req["msg"].get("sender_display_name")
                or req["msg"].get("sender_phone")
                or "?",
        })

    dead.sort(key=lambda d: d["sent_at"], reverse=True)

    by_site_counter: Counter[str] = Counter(d["site_name"] for d in dead)
    by_priority_counter: Counter[str] = Counter(d["priority"] or "?" for d in dead)

    return {
        "min_age_hours": min_age_hours,
        "window_hours": window_hours,
        "dead_count": len(dead),
        "by_site": [{"site_name": n, "count": k} for n, k in by_site_counter.most_common()],
        "by_priority": [{"priority": p, "count": k} for p, k in by_priority_counter.most_common()],
        "items": dead[:50],
    }


# ============================================================================
# 4. Heures critiques (heatmap 7×24)
# ============================================================================


_DAY_LABELS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]


def compute_critical_hours(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
    *,
    site_id: str | None = None,
) -> dict[str, Any]:
    """
    Construit une matrice 7×24 : pour chaque (jour-semaine, heure), nombre
    de messages urgent + high + action_required. Si site_id donné, filtre.
    """
    aliases_filter: list[str] | None = None
    if site_id:
        for s in sites:
            if s["id"] == site_id:
                aliases_filter = s.get("aliases") or []
                break

    # 7 jours × 24 heures, comptes par bucket
    heatmap = [[0 for _ in range(24)] for _ in range(7)]
    total = 0

    for m in messages:
        ts = m.get("_parsed_ts")
        if ts is None:
            continue
        c = classifications_by_id.get(m["id"])
        if not c:
            continue

        if aliases_filter is not None:
            ents_sites = (c.get("entities") or {}).get("sites") or []
            if not site_alias_match(ents_sites, aliases_filter):
                continue

        # critère "critique" : priority urgent OU high OU action_required
        is_critical = (
            c.get("priority") in ("urgent", "high")
            or c.get("action_required") is True
        )
        if not is_critical:
            continue

        dow = ts.weekday()  # 0=lundi
        hour = ts.hour
        heatmap[dow][hour] += 1
        total += 1

    # Top heures
    bucket_list: list[dict[str, Any]] = []
    for dow in range(7):
        for hour in range(24):
            if heatmap[dow][hour] > 0:
                bucket_list.append({
                    "day": _DAY_LABELS[dow],
                    "dow": dow,
                    "hour": hour,
                    "count": heatmap[dow][hour],
                })
    bucket_list.sort(key=lambda b: b["count"], reverse=True)

    return {
        "site_id": site_id,
        "total_critical_messages": total,
        "heatmap": heatmap,
        "day_labels": _DAY_LABELS,
        "top_buckets": bucket_list[:10],
    }
