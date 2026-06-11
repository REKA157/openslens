"""
Inventaire des incidents de débordement historiques (Sprint 1 Phase 2).

Scanne les messages WhatsApp + classifications IA et identifie ceux qui
mentionnent un débordement, saturation, ou risque imminent d'alvéole.

3 niveaux de confiance :
  - CONFIRMED : mention explicite d'un débordement constaté ("alvéole déborde",
                "déborde", "débordant", "à vider d'urgence")
  - LIKELY    : signal fort sans confirmation explicite ("alvéole pleine",
                "saturé", "presque plein", "urgent évacuation")
  - WEAK      : mention de panneaux sandwich avec urgence/action requise
                (potentiellement précurseur d'un débordement)

Le but : compter combien on a de CONFIRMED + LIKELY pour décider si on peut
entraîner un modèle ML supervisé (>=30 cas) ou si on reste sur des règles.

Pure agrégation, pas d'IA ici.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.routes.sites import site_alias_match

logger = logging.getLogger(__name__)


# Mots-clés de débordement, triés par confiance
# Tous compilés en regex case-insensitive, accents tolérés via .lower()

CONFIRMED_PATTERNS = [
    re.compile(r"\bdebord[ea]", re.IGNORECASE),         # déborde, débordant
    re.compile(r"\bdebordement\b", re.IGNORECASE),       # débordement
    re.compile(r"alveole[s]?\s+deborde", re.IGNORECASE), # alvéole déborde
    re.compile(r"a\s+vider\s+(?:d'?\s*)?urgen", re.IGNORECASE),  # à vider d'urgence
]

LIKELY_PATTERNS = [
    re.compile(r"alveole[s]?\s+(?:pleine|saturee|plein)", re.IGNORECASE),
    re.compile(r"alveole[s]?\s+(?:bloquee|bouchee)", re.IGNORECASE),
    re.compile(r"satur(?:e|ation)", re.IGNORECASE),
    re.compile(r"presque\s+plein", re.IGNORECASE),
    re.compile(r"plein[e]?\s+a\s+craquer", re.IGNORECASE),
    re.compile(r"urgen[ct].*evacuat", re.IGNORECASE),
    re.compile(r"benne\s+pleine", re.IGNORECASE),
]

WEAK_PATTERNS = [
    re.compile(r"panneaux?\s+sandwich.*urgent", re.IGNORECASE),
    re.compile(r"urgent.*panneaux?\s+sandwich", re.IGNORECASE),
    re.compile(r"vidange\s+urgente", re.IGNORECASE),
    re.compile(r"vider\s+rapidement", re.IGNORECASE),
    re.compile(r"a\s+vider", re.IGNORECASE),  # plus large que urgence
    re.compile(r"se\s+rempli", re.IGNORECASE),  # "se remplit"
]


def _normalize(s: str) -> str:
    """Strip accents et lower pour matching plus tolérant."""
    if not s:
        return ""
    # remplacer accents fréquents
    return (
        s.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("â", "a")
        .replace("ô", "o")
        .replace("î", "i")
        .replace("ç", "c")
        .lower()
    )


def _classify_text(text: str) -> tuple[str | None, list[str]]:
    """
    Renvoie (niveau, matched_keywords) pour un texte.
    Niveau ∈ {"confirmed", "likely", "weak", None}.
    """
    if not text:
        return None, []
    normalized = _normalize(text)
    matches: list[str] = []
    level: str | None = None

    for pattern in CONFIRMED_PATTERNS:
        m = pattern.search(normalized)
        if m:
            matches.append(m.group(0))
            level = "confirmed"

    if level != "confirmed":
        for pattern in LIKELY_PATTERNS:
            m = pattern.search(normalized)
            if m:
                matches.append(m.group(0))
                if level is None:
                    level = "likely"

    if level is None:
        for pattern in WEAK_PATTERNS:
            m = pattern.search(normalized)
            if m:
                matches.append(m.group(0))
                level = "weak"

    return level, matches


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _attribute_site(
    classification: dict | None,
    sites: list[dict],
) -> tuple[str | None, str | None]:
    """Renvoie (site_id, site_name) pour ce message, ou (None, None)."""
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


def extract_overflow_incidents(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
) -> dict[str, Any]:
    """
    Parcourt tous les messages, identifie les incidents de débordement,
    les attribue à un site (via la classification IA).

    Retourne :
      - incidents : liste détaillée (date, site, level, snippet, keywords)
      - summary   : compteurs par site × level + total
      - data_quality : indication de fiabilité de la labellisation
    """
    incidents: list[dict[str, Any]] = []
    for m in messages:
        text = m.get("raw_text") or ""
        if not text.strip():
            continue
        level, matches = _classify_text(text)
        if level is None:
            continue

        classification = classifications_by_id.get(m["id"])
        site_id, site_name = _attribute_site(classification, sites)

        ts = _parse_dt(m.get("sent_at"))
        snippet = text.strip().replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"

        incidents.append({
            "message_id": m["id"],
            "sent_at": ts.isoformat() if ts else None,
            "date": ts.date().isoformat() if ts else None,
            "site_id": site_id,
            "site_name": site_name or "?",
            "level": level,
            "matched_keywords": list(set(matches)),
            "sender": m.get("sender_display_name") or m.get("sender_phone") or "?",
            "snippet": snippet,
        })

    # Tri par date
    incidents.sort(key=lambda i: (i.get("date") or "", i["site_name"]))

    # Summary par site × level
    by_site: dict[str, dict[str, int]] = {}
    by_level: dict[str, int] = {"confirmed": 0, "likely": 0, "weak": 0}
    for inc in incidents:
        name = inc["site_name"]
        by_site.setdefault(name, {"confirmed": 0, "likely": 0, "weak": 0, "total": 0})
        by_site[name][inc["level"]] += 1
        by_site[name]["total"] += 1
        by_level[inc["level"]] += 1

    summary = {
        "total_incidents": len(incidents),
        "by_level": by_level,
        "by_site": dict(sorted(
            by_site.items(),
            key=lambda kv: kv[1]["total"],
            reverse=True,
        )),
        "unattributed_count": sum(1 for i in incidents if i["site_id"] is None),
    }

    # Évaluation qualité données pour décider ML vs règles
    n_strong = by_level["confirmed"] + by_level["likely"]
    if n_strong >= 30:
        data_quality = {
            "verdict": "ml_possible",
            "message": f"{n_strong} incidents confirmed+likely → ML supervisé envisageable.",
        }
    elif n_strong >= 10:
        data_quality = {
            "verdict": "ml_marginal",
            "message": (
                f"{n_strong} incidents confirmed+likely → ML possible mais "
                "modèle simple (Logistic Regression), pas de réseau de neurones."
            ),
        }
    else:
        data_quality = {
            "verdict": "rules_only",
            "message": (
                f"{n_strong} incidents confirmed+likely : trop peu pour ML. "
                "On basculera sur règles métier (seuils sur volume cumulé)."
            ),
        }

    return {
        "summary": summary,
        "data_quality": data_quality,
        "incidents": incidents,
    }
