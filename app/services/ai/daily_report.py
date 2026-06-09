"""
Génération du rapport quotidien narratif via Claude Sonnet.

Compile les messages classifiés d'une journée et demande à Claude un récit
factuel structuré (headline + narrative + points d'attention + activité site
+ actions ouvertes + recommandations). Stocke dans daily_reports.
"""

import json
import logging
from datetime import date, datetime, time, timezone
from typing import Any

import anthropic

from app.config import settings
from app.db import get_supabase

logger = logging.getLogger(__name__)


REPORT_MODEL = "claude-sonnet-4-5"


SYSTEM_PROMPT = """Tu es un analyste d'opérations terrain pour ADS, entreprise de \
collecte et évacuation de déchets en Île-de-France (PVC, ferraille, alu, gravats, \
bois, bennes grutables, multi-sites avec chauffeurs).

Ton rôle : produire un rapport quotidien synthétique exploitable à partir des \
messages WhatsApp pro reçus dans la journée. Le rapport doit être factuel, neutre, \
actionnable.

Règles strictes :
1. Tu réponds UNIQUEMENT par un objet JSON conforme au schéma fourni.
2. Aucun texte avant ou après le JSON. Pas de ```markdown.
3. Si une info n'est pas dans les messages, ne l'invente pas.
4. Tu ne juges JAMAIS individuellement les employés. Tu décris des faits opérationnels.
5. Le résumé est en français, neutre, factuel.

Schéma JSON attendu EXACTEMENT :
{
  "headline": "<1 phrase qui résume la journée, max 150 caractères>",
  "narrative": "<3-5 phrases de récit factuel des grands sujets du jour>",
  "urgent_points": [
    "<point critique 1>",
    "<point critique 2>"
  ],
  "site_activity": {
    "<NomSite1>": "<résumé activité site>",
    "<NomSite2>": "<résumé activité site>"
  },
  "open_actions": [
    "<action ouverte en fin de journée 1>",
    "<action ouverte en fin de journée 2>"
  ],
  "recommendations": [
    "<reco organisationnelle 1>",
    "<reco organisationnelle 2>"
  ]
}

Contraintes :
- `urgent_points` : max 5, focalise sur incidents, urgences, pollution, pannes.
- `site_activity` : top 3-5 sites les plus actifs/critiques. Clé = nom de site.
- `open_actions` : max 5, ce qui semble non clos en fin de journée.
- `recommendations` : max 3, suggestions sur les PROCESSUS pas sur les personnes."""


async def generate_daily_report(target_date: date, *, force: bool = False) -> dict:
    """
    Génère le rapport pour `target_date` (UTC). Si un rapport existe déjà
    pour cette date et force=False, le retourne sans appeler Claude.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY non configurée")

    sb = get_supabase()

    # 1. Vérifier si rapport existant
    existing = (
        sb.table("daily_reports")
        .select("*")
        .eq("company_id", settings.company_id)
        .eq("report_date", target_date.isoformat())
        .limit(1)
        .execute()
    )
    if existing.data and not force:
        logger.info("Rapport déjà existant pour %s, retour cache", target_date)
        return existing.data[0]

    # 2. Récupérer les messages du jour
    day_start = datetime.combine(target_date, time.min, tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time.max, tzinfo=timezone.utc)

    msgs = (
        sb.table("whatsapp_messages")
        .select("id,sent_at,sender_phone,sender_display_name,raw_text,message_type")
        .order("sent_at")
        .limit(500)
        .execute()
    )

    def _parse_dt(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    day_msgs = []
    for m in msgs.data or []:
        ts = _parse_dt(m.get("sent_at"))
        if ts and day_start <= ts <= day_end:
            day_msgs.append(m)

    if not day_msgs:
        raise ValueError(f"Aucun message pour {target_date}, rien à résumer")

    # 3. Récupérer leurs classifications
    msg_ids = [m["id"] for m in day_msgs]
    classifications = []
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

    # 4. Stats agrégées
    stats: dict[str, Any] = {
        "total_messages": len(day_msgs),
        "by_priority": {},
        "by_category": {},
        "action_required_count": 0,
        "sites_mentioned": [],
    }
    site_set: set[str] = set()
    for m in day_msgs:
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

    # 5. Construire le contexte pour Claude
    lines = []
    for m in day_msgs:
        c = class_by_msg.get(m["id"]) or {}
        sender = m.get("sender_display_name") or m.get("sender_phone") or "?"
        ts = _parse_dt(m["sent_at"])
        ts_str = ts.strftime("%H:%M") if ts else "?"
        cat = c.get("business_category") or "-"
        prio = c.get("priority") or "-"
        summary = c.get("summary") or (m.get("raw_text") or "")[:200]
        action = " [action]" if c.get("action_required") else ""
        lines.append(f"- {ts_str} {sender} [{cat}/{prio}]{action} : {summary}")

    context = (
        f"Date : {target_date.isoformat()}\n"
        f"Total messages : {stats['total_messages']}\n"
        f"Répartition priorités : {stats['by_priority']}\n"
        f"Répartition catégories : {stats['by_category']}\n"
        f"Sites mentionnés : {', '.join(stats['sites_mentioned'][:20]) or 'aucun nommément identifié'}\n"
        f"\nListe des messages classifiés (heure, expéditeur, catégorie/priorité, résumé) :\n"
        + "\n".join(lines)
    )

    # 6. Appeler Claude Sonnet
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=REPORT_MODEL,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Voici l'activité de la journée. Produis le rapport.\n\n{context}"
                    ),
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

    # 7. Upsert dans daily_reports
    row = {
        "company_id": settings.company_id,
        "report_date": target_date.isoformat(),
        "content": content,
        "model_used": REPORT_MODEL,
        "stats": stats,
    }
    upserted = (
        sb.table("daily_reports")
        .upsert(row, on_conflict="company_id,report_date")
        .execute()
    )
    logger.info("Rapport quotidien %s généré (%d messages)", target_date, len(day_msgs))
    return upserted.data[0] if upserted.data else row
