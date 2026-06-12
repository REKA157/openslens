"""
Génération de rapports narratifs (jour / semaine / mois) via Claude Sonnet.

Compile les messages classifiés d'une période et demande à Claude un récit
factuel structuré. Stocke dans la table daily_reports avec period_type +
period_end (cf migration_v4_period_reports.sql).

Schéma JSON renvoyé par Claude (commun aux 3 périodes pour compat UI) :
  headline, narrative, urgent_points, site_activity, open_actions, recommendations
"""

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

import anthropic

from app.config import settings
from app.db import get_supabase
from app.services.analytics import quantitative

logger = logging.getLogger(__name__)


PeriodType = Literal["day", "week", "month"]
REPORT_MODEL = "claude-sonnet-4-5"


# --- Fenêtres temporelles ----------------------------------------------------


def compute_window(target_date: date, period: PeriodType) -> tuple[date, date]:
    """Retourne (period_start, period_end) inclusives pour la période donnée."""
    if period == "day":
        return target_date, target_date
    if period == "week":
        # Lundi → dimanche de la semaine de target_date
        monday = target_date - timedelta(days=target_date.weekday())
        sunday = monday + timedelta(days=6)
        return monday, sunday
    if period == "month":
        first = target_date.replace(day=1)
        if first.month == 12:
            next_first = first.replace(year=first.year + 1, month=1)
        else:
            next_first = first.replace(month=first.month + 1)
        last = next_first - timedelta(days=1)
        return first, last
    raise ValueError(f"period inconnu : {period}")


# --- Prompts -----------------------------------------------------------------

_COMMON_SCHEMA = """Schéma JSON attendu EXACTEMENT :
{
  "headline": "<1 phrase qui résume la période, max 160 caractères>",
  "narrative": "<récit factuel des grands sujets>",
  "urgent_points": [
    "<point critique 1>"
  ],
  "site_activity": {
    "<NomSite>": "<résumé activité site>"
  },
  "open_actions": [
    "<action ouverte 1>"
  ],
  "recommendations": [
    "<reco organisationnelle 1>"
  ]
}

Règles strictes :
1. Tu réponds UNIQUEMENT par un objet JSON conforme au schéma.
2. Aucun texte avant ou après le JSON. Pas de ```markdown.
3. Si une info n'est pas dans les messages, ne l'invente pas.
4. Tu ne juges JAMAIS individuellement les employés. Tu décris des faits opérationnels.
5. Le rapport est en français, neutre, factuel."""


SYSTEM_PROMPTS: dict[PeriodType, str] = {
    "day": f"""Tu es un analyste d'opérations terrain pour ADS, entreprise de \
collecte et évacuation de déchets en Île-de-France (PVC, ferraille, alu, gravats, \
bois, bennes grutables, multi-sites avec chauffeurs).

Ton rôle : produire un rapport QUOTIDIEN synthétique exploitable à partir des \
messages WhatsApp pro reçus dans la journée. Le rapport doit être factuel, neutre, \
actionnable.

Cibles :
- narrative : 3 à 5 phrases sur les grands sujets du jour.
- urgent_points : max 5, focalise sur incidents, urgences, pollution, pannes.
- site_activity : top 3-5 sites les plus actifs/critiques.
- open_actions : max 5, ce qui semble non clos en fin de journée.
- recommendations : max 3, sur les PROCESSUS pas sur les personnes.

{_COMMON_SCHEMA}""",

    "week": f"""Tu es un analyste d'opérations terrain pour ADS, entreprise de \
collecte et évacuation de déchets en Île-de-France.

Ton rôle : produire un rapport HEBDOMADAIRE de synthèse à partir des messages \
WhatsApp pro reçus pendant la semaine. Il s'adresse à un responsable d'exploitation \
qui veut voir les tendances, les sujets récurrents et les arbitrages à faire.

Cibles :
- narrative : 6 à 10 phrases. Décris les fils rouges de la semaine, les sites les \
plus chargés, les types de demandes qui dominent, et les jours / moments forts.
- urgent_points : max 6. Mets en avant les incidents répétés ou non résolus, \
pas le bruit ponctuel.
- site_activity : top 5-8 sites, en évoquant pour chacun le volume et le type \
d'activité dominante sur la semaine.
- open_actions : max 6, actions ouvertes au moment du rapport.
- recommendations : max 4, sur les processus à ajuster cette semaine (planning, \
ressources, dispatch).

{_COMMON_SCHEMA}""",

    "month": f"""Tu es un analyste d'opérations terrain pour ADS, entreprise de \
collecte et évacuation de déchets en Île-de-France.

Ton rôle : produire un rapport MENSUEL de pilotage à partir des messages WhatsApp \
pro reçus pendant le mois. Il s'adresse à la direction qui veut une vue d'ensemble \
des activités, des tensions, et des points de pilotage à prévoir.

Cibles :
- narrative : 10 à 15 phrases. Vision d'ensemble du mois : quels sites ont concentré \
l'activité, quels types de demandes ont dominé, quelles tendances vs un mois normal, \
quels sujets ont demandé du temps.
- urgent_points : max 8. Sujets structurants, pas anecdotiques. Mets l'accent sur \
ce qui revient ou pose problème sur la durée.
- site_activity : top 8-12 sites, avec volume et nature de l'activité du mois.
- open_actions : max 8, actions ouvertes en fin de mois.
- recommendations : max 5, recommandations de pilotage (réorganisation, ajout de \
moyens, ajustement processus).

{_COMMON_SCHEMA}""",
}


# --- Génération --------------------------------------------------------------


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


async def generate_report(
    target_date: date,
    period: PeriodType = "day",
    *,
    force: bool = False,
) -> dict:
    """
    Génère le rapport pour la période contenant `target_date`.
    Pour day  : la journée de target_date.
    Pour week : semaine lundi→dimanche contenant target_date.
    Pour month: mois calendaire contenant target_date.

    `report_date` stocké en DB = period_start (lundi pour week, 1er pour month).
    Si force=False et qu'un rapport existe déjà pour ce (period, report_date),
    le retourne tel quel sans appeler Claude.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY non configurée")

    period_start, period_end = compute_window(target_date, period)
    sb = get_supabase()

    # 1. Cache
    existing = (
        sb.table("daily_reports")
        .select("*")
        .eq("company_id", settings.company_id)
        .eq("period_type", period)
        .eq("report_date", period_start.isoformat())
        .limit(1)
        .execute()
    )
    if existing.data and not force:
        logger.info(
            "Rapport %s %s déjà existant, retour cache", period, period_start
        )
        return existing.data[0]

    # 2. Messages de la fenêtre
    win_start = datetime.combine(period_start, time.min, tzinfo=timezone.utc)
    win_end = datetime.combine(period_end, time.max, tzinfo=timezone.utc)

    # Pour les rapports mois on peut taper jusqu'à ~3000 messages, on remonte
    # la limite mais on garde un plafond pour éviter d'exploser le contexte.
    msg_limit = {"day": 500, "week": 1500, "month": 4000}[period]
    msgs = (
        sb.table("whatsapp_messages")
        .select("id,sent_at,sender_phone,sender_display_name,raw_text,message_type")
        .order("sent_at")
        .limit(msg_limit)
        .execute()
    )

    window_msgs = []
    for m in msgs.data or []:
        ts = _parse_dt(m.get("sent_at"))
        if ts and win_start <= ts <= win_end:
            window_msgs.append(m)

    if not window_msgs:
        raise ValueError(
            f"Aucun message pour {period} {period_start}→{period_end}, rien à résumer"
        )

    # 3. Classifications
    msg_ids = [m["id"] for m in window_msgs]
    classifications: list[dict] = []
    for i in range(0, len(msg_ids), 100):
        chunk = msg_ids[i : i + 100]
        res = (
            sb.table("message_classifications")
            .select("*")
            .in_("message_id", chunk)
            .execute()
        )
        classifications.extend(res.data or [])
    class_by_msg = {c["message_id"]: c for c in classifications}

    # 4. Stats
    stats: dict[str, Any] = {
        "period_type": period,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "total_messages": len(window_msgs),
        "by_priority": {},
        "by_category": {},
        "action_required_count": 0,
        "sites_mentioned": [],
    }
    site_set: set[str] = set()
    for m in window_msgs:
        c = class_by_msg.get(m["id"])
        if not c:
            continue
        prio = c.get("priority") or "?"
        cat = c.get("business_category") or "?"
        stats["by_priority"][prio] = stats["by_priority"].get(prio, 0) + 1
        stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
        if c.get("action_required"):
            stats["action_required_count"] += 1
        entities = c.get("entities") or {}
        for s in entities.get("sites") or []:
            if isinstance(s, str) and s.strip():
                site_set.add(s.strip())
    stats["sites_mentioned"] = sorted(site_set)

    # 4b. Données QUANTITATIVES MKGT de la période (tonnage / € réels)
    quant = None
    quant_block = ""
    try:
        sites_q = (
            sb.table("sites")
            .select("id,canonical_name,aliases,region")
            .eq("company_id", settings.company_id)
            .eq("is_active", True)
            .execute()
        ).data or []
        mkgt_ops = quantitative.load_mkgt_operations(period_start, period_end)
        if mkgt_ops:
            quant = quantitative.aggregate_by_site(mkgt_ops, sites_q)
            stats["quantitative"] = quant["totals"]
            stats["quantitative_by_site"] = [
                {
                    "site_name": s["site_name"],
                    "tonnage": s["tonnage"],
                    "amount_ht": s["amount_ht"],
                    "operations": s["operations"],
                }
                for s in quant["by_site"][:15]
            ]
            t = quant["totals"]
            site_lines = [
                f"- {s['site_name']} : {s['tonnage']} t, {s['amount_ht']} € HT, {s['operations']} op."
                for s in quant["by_site"][:15]
            ]
            quant_block = (
                "\n\nDONNÉES QUANTITATIVES MKGT (chiffres réels de la période) :\n"
                f"Total : {t['tonnage']} tonnes collectées, {t['amount_ht']} € HT, "
                f"{t['operations']} opérations.\n"
                "Par site :\n" + "\n".join(site_lines)
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Quantitatif rapport indisponible: %s", exc)

    # 5. Contexte pour Claude
    # Pour week/month on date chaque ligne en jour+heure (sinon on perd la
    # notion de "quand" dans le récit).
    show_date = period != "day"
    lines = []
    for m in window_msgs:
        c = class_by_msg.get(m["id"]) or {}
        sender = m.get("sender_display_name") or m.get("sender_phone") or "?"
        ts = _parse_dt(m["sent_at"])
        ts_str = (
            ts.strftime("%d/%m %H:%M" if show_date else "%H:%M")
            if ts
            else "?"
        )
        cat = c.get("business_category") or "-"
        prio = c.get("priority") or "-"
        summary = c.get("summary") or (m.get("raw_text") or "")[:200]
        action = " [action]" if c.get("action_required") else ""
        lines.append(f"- {ts_str} {sender} [{cat}/{prio}]{action} : {summary}")

    period_label_fr = {
        "day": f"Journée du {period_start.isoformat()}",
        "week": f"Semaine du {period_start.isoformat()} au {period_end.isoformat()}",
        "month": f"Mois de {period_start.strftime('%B %Y')} ({period_start.isoformat()} → {period_end.isoformat()})",
    }[period]

    context = (
        f"Période : {period_label_fr}\n"
        f"Total messages : {stats['total_messages']}\n"
        f"Répartition priorités : {stats['by_priority']}\n"
        f"Répartition catégories : {stats['by_category']}\n"
        f"Sites mentionnés : {', '.join(stats['sites_mentioned'][:30]) or 'aucun nommément identifié'}\n"
        f"{quant_block}\n"
        f"\nListe des messages classifiés :\n" + "\n".join(lines)
    )
    if quant_block:
        context += (
            "\n\nNB : intègre les chiffres MKGT (tonnages, € HT) dans le récit et "
            "site_activity quand ils sont disponibles — ce sont les volumes réels."
        )

    # 6. Appel Claude
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=REPORT_MODEL,
            max_tokens={"day": 2048, "week": 3072, "month": 4096}[period],
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPTS[period],
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"Voici l'activité de la période. Produis le rapport.\n\n{context}",
                }
            ],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        content = json.loads(raw)
    finally:
        await client.close()

    # 7. Upsert
    row = {
        "company_id": settings.company_id,
        "period_type": period,
        "report_date": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "content": content,
        "model_used": REPORT_MODEL,
        "stats": stats,
    }
    upserted = (
        sb.table("daily_reports")
        .upsert(row, on_conflict="company_id,period_type,report_date")
        .execute()
    )
    logger.info(
        "Rapport %s %s→%s généré (%d messages)",
        period, period_start, period_end, len(window_msgs),
    )
    return upserted.data[0] if upserted.data else row


# --- Backward compat : ancien nom utilisé par routes/reports.py ---------------

async def generate_daily_report(target_date: date, *, force: bool = False) -> dict:
    """Alias rétrocompatible — équivalent à generate_report(period='day')."""
    return await generate_report(target_date, "day", force=force)
