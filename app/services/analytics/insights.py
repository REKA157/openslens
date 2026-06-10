"""
Insights IA croisés : prend les signaux statistiques + un échantillon des
messages WhatsApp pros récents, et fait dire à Claude Sonnet ce qu'il faut
décider concrètement pour ADS.

Sortie structurée :
  - narrative_overview         : synthèse globale du moment opérationnel
  - alerts[]                   : alertes priorisées (critical / warning / info)
                                 avec catégorie business + actions recommandées
  - cross_signals[]            : insights composites multi-sites
  - recommendations_by_site    : décisions concrètes par site

Pas de cache DB : le coût Claude reste raisonnable (~0,05–0,10 $ par appel).
"""

from __future__ import annotations

import json
import logging
from datetime import date as date_cls, datetime, time, timedelta, timezone
from typing import Any

import anthropic

from app.config import settings
from app.routes.sites import site_alias_match

logger = logging.getLogger(__name__)


INSIGHT_MODEL = "claude-sonnet-4-5"


SYSTEM_PROMPT = """Tu es analyste expert en exploitation pour ADS, entreprise \
française de collecte et évacuation de déchets en Île-de-France (PVC, ferraille, \
alu, gravats, bois, bennes grutables 15m³ et 30m³, panneaux sandwich, batteries/\
calculateurs VHU, multi-sites avec chauffeurs et mécaniciens).

Tu reçois en entrée :
1. Des SIGNAUX STATISTIQUES (anomalies Z-score, tendances en %, prévisions \
hebdo, pannes récurrentes)
2. Un ÉCHANTILLON DES MESSAGES WhatsApp pros récents (texte + classification IA)
   pour chaque site

Ton rôle : produire une analyse opérationnelle ACTIONNABLE pour le pilote \
d'exploitation ADS. Tu dois :
- Croiser les signaux quantitatifs avec les messages qualitatifs (la réalité)
- Identifier ce qui mérite une attention IMMÉDIATE
- Proposer des décisions CONCRÈTES : qui appeler, quoi décider, sous quel délai
- Repérer les signaux composites (plusieurs facteurs qui pointent dans la même direction)

Connaissance métier ADS :
- Activité dominante : collecte de bennes (DU, DEE 15m³, ferrailles, gravats, \
bois, plâtres, panneaux sandwich, câbles cuivres) sur chantiers/sites clients
- Sites principaux par volume : Le Plessis-Belleville (IDF Nord, le plus gros), \
Saint-Leu-la-Forêt, Élancourt, Viry-Châtillon, Dreux
- Acteurs : chauffeurs, mécaniciens (révisions Liebherr), dispatchers, \
responsables exploitation par zone IDF Nord / IDF Sud
- Engins critiques : pelles Liebherr (LH30 etc), camions Derichebourg 30m³
- Risques opérationnels MAJEURS :
  * Débordement alvéole panneaux sandwich → pollution + arrêt site
  * Panne pelle / mécanicien indisponible → blocage chantier
  * Absence de benne au bon moment → client mécontent / pénalité
  * Saturation site (trop de bennes pleines en attente) → embolie

Règles strictes :
1. Tu réponds UNIQUEMENT en JSON conforme au schéma. Aucun texte autour.
2. Pas de ```markdown.
3. Tes recommandations sont ACTIONNABLES : qui appeler, quoi décider, sous quel délai.
4. Pas de jargon corporate vague. Du concret terrain ADS.
5. Si une alerte cite un site, mets son site_id EXACT depuis l'entrée. Sinon null.
6. severity = "critical" si risque pollution/sécurité/arrêt site imminent
            = "warning" si tension opérationnelle nécessitant arbitrage cette semaine
            = "info" si signal à surveiller, pas d'urgence
7. category : "surcharge" (volume anormal), "qualite_securite" (pollution/incident), \
"equipement" (pannes/maintenance), "silence_anormal" (site qui ne parle plus), \
"opportunite" (croissance qui justifie d'investir)

Schéma JSON attendu EXACTEMENT :
{
  "narrative_overview": "<2-3 phrases de synthèse globale du moment ADS>",
  "alerts": [
    {
      "site_id": "<uuid du site OU null si transversal>",
      "site_name": "<nom canonique ou 'Multi-sites' si null>",
      "severity": "critical|warning|info",
      "category": "surcharge|qualite_securite|equipement|silence_anormal|opportunite",
      "title": "<phrase courte percutante, max 80 caractères>",
      "evidence": "<2-3 phrases factuelles citant chiffres ET messages réels>",
      "recommended_actions": [
        "<action 1 : qui appeler / quoi décider / délai>",
        "<action 2>",
        "<action 3>"
      ],
      "timeline": "immediat|cette_semaine|ce_mois"
    }
  ],
  "cross_signals": [
    {
      "title": "<phrase courte, max 80 caractères>",
      "involved_sites": ["<nom canonique 1>", "<nom canonique 2>"],
      "explanation": "<2-3 phrases sur ce que révèle le croisement>",
      "implications": "<conséquences opérationnelles concrètes>"
    }
  ],
  "recommendations_by_site": {
    "<nom_canonique>": ["<reco 1>", "<reco 2>"]
  }
}

Cibles quantitatives :
- alerts : 3 à 8 alertes maximum, priorisées (critical d'abord). \
Au moins 1 alert critical s'il y a un VRAI signal fort (Z>=3, débordement, panne grave).
- cross_signals : 0 à 3, seulement si VRAIE corrélation observée.
- recommendations_by_site : 3 sites max les plus pertinents."""


def gather_context_per_site(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    sites: list[dict],
    ref_date: date_cls,
    *,
    days_back: int = 14,
    max_per_site: int = 15,
) -> dict[str, dict[str, Any]]:
    """
    Pour chaque site, récupère les N derniers messages classifiés des
    `days_back` derniers jours, avec leur catégorie/priorité/résumé.
    Sert à donner à Claude la matière qualitative pour interpréter les chiffres.
    """
    cutoff = datetime.combine(
        ref_date - timedelta(days=days_back), time.min, tzinfo=timezone.utc,
    )

    by_site: dict[str, dict[str, Any]] = {
        s["id"]: {"name": s["canonical_name"], "messages": []}
        for s in sites
    }

    for m in messages:
        ts: datetime | None = m.get("_parsed_ts")
        if not ts or ts < cutoff:
            continue
        c = classifications_by_id.get(m["id"])
        if not c:
            continue
        ents_sites = (c.get("entities") or {}).get("sites") or []
        if not ents_sites:
            continue
        for s in sites:
            aliases = s.get("aliases") or []
            if site_alias_match(ents_sites, aliases):
                by_site[s["id"]]["messages"].append({
                    "date": ts.strftime("%Y-%m-%d %H:%M"),
                    "category": c.get("business_category"),
                    "priority": c.get("priority"),
                    "summary": (
                        c.get("summary")
                        or (m.get("raw_text") or "")[:200]
                    ),
                    "action_required": c.get("action_required"),
                })
                break  # on évite de doubler un message dans plusieurs sites

    # Garde les `max_per_site` plus récents par site (tri date décroissant)
    for site_id, ctx in by_site.items():
        ctx["messages"] = sorted(
            ctx["messages"],
            key=lambda x: x["date"],
            reverse=True,
        )[:max_per_site]

    return by_site


def _format_messages_block(by_site_ctx: dict[str, dict[str, Any]]) -> str:
    out_lines: list[str] = []
    for site_id, ctx in by_site_ctx.items():
        if not ctx["messages"]:
            continue
        out_lines.append(f"\n--- {ctx['name']} (id={site_id}) ---")
        for msg in ctx["messages"]:
            action = " [ACTION]" if msg["action_required"] else ""
            out_lines.append(
                f"  [{msg['date']}] [{msg['priority']}/{msg['category']}]{action} : "
                f"{msg['summary']}"
            )
    return "\n".join(out_lines)


async def generate_insights(
    predictive_data: dict[str, Any],
    sites: list[dict],
    contextual_samples: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Appelle Claude Sonnet avec les signaux statistiques + les samples
    qualitatifs. Renvoie le JSON structuré insights (alerts/cross/recos).
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY non configurée")

    user_content_parts: list[str] = [
        "SIGNAUX STATISTIQUES :",
        "",
        "## Anomalies (semaine courante)",
        json.dumps(predictive_data.get("anomalies", []), ensure_ascii=False, indent=2),
        "",
        "## Tendances (28 j vs 28 j précédents)",
        json.dumps(predictive_data.get("trends", []), ensure_ascii=False, indent=2),
        "",
        "## Prévisions semaine prochaine",
        json.dumps(predictive_data.get("forecast", []), ensure_ascii=False, indent=2),
        "",
        "## Pannes récurrentes (3 mois)",
        json.dumps(predictive_data.get("recurring_failures", []), ensure_ascii=False, indent=2),
        "",
        "",
        "ÉCHANTILLON QUALITATIF — messages WhatsApp pros récents par site (14 derniers jours, max 15 par site) :",
        _format_messages_block(contextual_samples),
    ]
    user_content = "\n".join(user_content_parts)

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=INSIGHT_MODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.exception("Claude a renvoyé un JSON invalide : %s", exc)
        raise
    finally:
        await client.close()

    # Validation et défauts
    if not isinstance(result, dict):
        raise ValueError("Claude n'a pas renvoyé un objet JSON")
    result.setdefault("narrative_overview", "")
    result.setdefault("alerts", [])
    result.setdefault("cross_signals", [])
    result.setdefault("recommendations_by_site", {})
    return result
