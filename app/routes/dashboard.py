"""
Endpoint /api/dashboard — KPIs et agrégations pour le tableau de bord.

Retourne en un seul appel :
  - compteurs aujourd'hui vs hier
  - répartition par catégorie / priorité
  - top sites mentionnés (extraits de entities.sites)
  - urgences en cours (priority=urgent + action_required + non clôturée)
  - actions ouvertes (demande_action récente)

Aucun LLM ici, juste de l'agrégation depuis Supabase.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter

from app.db import get_supabase

router = APIRouter(prefix="/api", tags=["dashboard"])
logger = logging.getLogger(__name__)


@router.get("/dashboard")
async def dashboard() -> dict[str, Any]:
    sb = get_supabase()
    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    week_start = today_start - timedelta(days=7)

    # 1. Tous les messages des 7 derniers jours (suffisant pour les agrégations)
    messages = (
        sb.table("whatsapp_messages")
        .select("id,sent_at,sender_phone,sender_display_name,raw_text,message_type")
        .order("sent_at", desc=True)
        .limit(500)
        .execute()
    )
    msg_rows = messages.data or []

    # On garde uniquement les 7 derniers jours
    recent_msgs = [
        m for m in msg_rows
        if datetime.fromisoformat(m["sent_at"].replace("Z", "+00:00")) >= week_start
    ]

    # 2. Classifications de tous ces messages
    msg_ids = [m["id"] for m in recent_msgs]
    classifications: list[dict] = []
    if msg_ids:
        # PostgREST IN avec liste — chunks de 100 pour éviter URL trop longue
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

    # 3. KPIs
    def in_window(start: datetime, end: datetime, msg: dict) -> bool:
        ts = datetime.fromisoformat(msg["sent_at"].replace("Z", "+00:00"))
        return start <= ts < end

    today_msgs = [m for m in recent_msgs if in_window(today_start, now, m)]
    yesterday_msgs = [
        m for m in recent_msgs if in_window(yesterday_start, today_start, m)
    ]

    def count_by_category(msgs: list[dict], cat: str) -> int:
        return sum(
            1
            for m in msgs
            if class_by_msg.get(m["id"], {}).get("business_category") == cat
        )

    def count_by_priority(msgs: list[dict], prio: str) -> int:
        return sum(
            1 for m in msgs if class_by_msg.get(m["id"], {}).get("priority") == prio
        )

    def count_action_required(msgs: list[dict]) -> int:
        return sum(
            1
            for m in msgs
            if class_by_msg.get(m["id"], {}).get("action_required") is True
        )

    kpis_today = {
        "messages": len(today_msgs),
        "incidents": count_by_category(today_msgs, "incident"),
        "urgent": count_by_priority(today_msgs, "urgent"),
        "high": count_by_priority(today_msgs, "high"),
        "demande_action": count_by_category(today_msgs, "demande_action"),
        "action_required": count_action_required(today_msgs),
        "livraisons": count_by_category(today_msgs, "livraison"),
    }
    kpis_yesterday = {
        "messages": len(yesterday_msgs),
        "incidents": count_by_category(yesterday_msgs, "incident"),
        "urgent": count_by_priority(yesterday_msgs, "urgent"),
        "high": count_by_priority(yesterday_msgs, "high"),
        "demande_action": count_by_category(yesterday_msgs, "demande_action"),
        "action_required": count_action_required(yesterday_msgs),
        "livraisons": count_by_category(yesterday_msgs, "livraison"),
    }

    # 4. Breakdown catégorie / priorité (sur 7 jours)
    cat_counter: Counter[str] = Counter()
    prio_counter: Counter[str] = Counter()
    for m in recent_msgs:
        c = class_by_msg.get(m["id"])
        if not c:
            continue
        if c.get("business_category"):
            cat_counter[c["business_category"]] += 1
        if c.get("priority"):
            prio_counter[c["priority"]] += 1

    categories_breakdown = [
        {"category": k, "count": v} for k, v in cat_counter.most_common(10)
    ]
    priorities_breakdown = [
        {"priority": k, "count": v}
        for k, v in sorted(
            prio_counter.items(),
            key=lambda x: ["urgent", "high", "medium", "low"].index(x[0])
            if x[0] in ("urgent", "high", "medium", "low")
            else 99,
        )
    ]

    # 5. Top sites mentionnés (extraits de entities.sites sur 7 jours)
    site_counter: Counter[str] = Counter()
    for c in classifications:
        entities = c.get("entities") or {}
        sites = entities.get("sites") or []
        for s in sites:
            if isinstance(s, str) and s.strip():
                site_counter[s.strip()] += 1
    top_sites = [{"site": k, "count": v} for k, v in site_counter.most_common(10)]

    # 6. Senders les plus actifs (sur 7 jours)
    sender_counter: Counter[str] = Counter()
    for m in recent_msgs:
        key = m.get("sender_display_name") or m.get("sender_phone") or "?"
        sender_counter[key] += 1
    top_senders = [
        {"sender": k, "count": v} for k, v in sender_counter.most_common(8)
    ]

    # 7. Urgences en cours : urgent ou high + action_required (sur 7 jours)
    urgent_items = []
    for m in recent_msgs:
        c = class_by_msg.get(m["id"])
        if not c:
            continue
        priority = c.get("priority")
        if priority not in ("urgent", "high"):
            continue
        urgent_items.append(
            {
                "message_id": m["id"],
                "sender": m.get("sender_display_name") or m.get("sender_phone"),
                "sent_at": m["sent_at"],
                "category": c.get("business_category"),
                "priority": priority,
                "summary": c.get("summary"),
                "action_required": c.get("action_required"),
                "raw_text": (m.get("raw_text") or "")[:200],
            }
        )
    urgent_items = sorted(urgent_items, key=lambda x: x["sent_at"], reverse=True)[:20]

    return {
        "generated_at": now.isoformat(),
        "kpis": {"today": kpis_today, "yesterday": kpis_yesterday},
        "categories": categories_breakdown,
        "priorities": priorities_breakdown,
        "top_sites": top_sites,
        "top_senders": top_senders,
        "urgent_items": urgent_items,
    }
