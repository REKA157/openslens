"""
Endpoint /api/dashboard — KPIs et agrégations pour le tableau de bord.

Paramètres :
  - date (YYYY-MM-DD, optionnel) : date de référence pour la fenêtre principale.
                                   Défaut : aujourd'hui (UTC).
  - period (day | week | month) : granularité de la fenêtre. Défaut : day.

Retourne :
  - kpis.current / kpis.previous : compteurs sur la fenêtre demandée et la
    fenêtre précédente de même longueur (jour-1, semaine-1, mois-1).
  - categories / priorities : répartition sur ~30 jours glissants (contexte)
  - top_sites / top_senders : idem
  - urgent_items : items urgent/high sur ~30 jours glissants

Aucun LLM ici, pure agrégation.
"""

import logging
from collections import Counter
from datetime import date as date_cls, datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.db import get_supabase

router = APIRouter(prefix="/api", tags=["dashboard"])
logger = logging.getLogger(__name__)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _window(period: str, ref: date_cls) -> tuple[datetime, datetime, datetime, datetime, str]:
    """
    Pour une date de référence et une période, retourne
    (current_start, current_end, previous_start, previous_end, label).
    Bornes : current_end exclusive (start <= ts < end).
    """
    tz = timezone.utc

    if period == "day":
        cur_start = datetime.combine(ref, time.min, tzinfo=tz)
        cur_end = cur_start + timedelta(days=1)
        prev_start = cur_start - timedelta(days=1)
        prev_end = cur_start
        label = ref.isoformat()

    elif period == "week":
        # Semaine ISO : lundi de la semaine de ref
        weekday = ref.weekday()  # 0 = lundi
        monday = ref - timedelta(days=weekday)
        cur_start = datetime.combine(monday, time.min, tzinfo=tz)
        cur_end = cur_start + timedelta(days=7)
        prev_start = cur_start - timedelta(days=7)
        prev_end = cur_start
        label = f"Semaine du {monday.isoformat()}"

    elif period == "month":
        first_of_month = ref.replace(day=1)
        cur_start = datetime.combine(first_of_month, time.min, tzinfo=tz)
        # Premier jour du mois suivant
        if first_of_month.month == 12:
            next_first = first_of_month.replace(year=first_of_month.year + 1, month=1)
        else:
            next_first = first_of_month.replace(month=first_of_month.month + 1)
        cur_end = datetime.combine(next_first, time.min, tzinfo=tz)
        # Mois précédent
        if first_of_month.month == 1:
            prev_first = first_of_month.replace(year=first_of_month.year - 1, month=12)
        else:
            prev_first = first_of_month.replace(month=first_of_month.month - 1)
        prev_start = datetime.combine(prev_first, time.min, tzinfo=tz)
        prev_end = cur_start
        label = first_of_month.strftime("%Y-%m")

    else:
        raise ValueError(f"period inconnu : {period}")

    return cur_start, cur_end, prev_start, prev_end, label


@router.get("/dashboard")
async def dashboard(
    date: str | None = Query(default=None, description="YYYY-MM-DD ; défaut = aujourd'hui UTC"),
    period: str = Query(default="day", description="day | week | month"),
    site_id: str | None = Query(default=None, description="Filtrer sur un site canonique"),
) -> dict[str, Any]:
    if period not in ("day", "week", "month"):
        raise HTTPException(400, detail="period doit être day, week ou month")

    if date:
        try:
            ref = date_cls.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, detail="date doit être YYYY-MM-DD") from None
    else:
        ref = datetime.now(tz=timezone.utc).date()

    cur_start, cur_end, prev_start, prev_end, label = _window(period, ref)

    # Filtre site (si demandé) : on récupère les aliases du site
    site_aliases: list[str] | None = None
    if site_id:
        # Import local pour éviter une dépendance circulaire au load
        from app.routes.sites import fetch_site_aliases

        site_aliases = fetch_site_aliases(site_id)
        if site_aliases is None:
            raise HTTPException(404, detail="Site introuvable")

    sb = get_supabase()

    # On charge un horizon large (30 jours en arrière par rapport au début de la fenêtre)
    horizon_start = prev_start - timedelta(days=30)

    messages = (
        sb.table("whatsapp_messages")
        .select("id,sent_at,sender_phone,sender_display_name,raw_text,message_type")
        .order("sent_at", desc=True)
        .limit(2000)
        .execute()
    )
    msg_rows = messages.data or []

    recent_msgs = []
    for m in msg_rows:
        ts = _parse_dt(m.get("sent_at"))
        if ts and ts >= horizon_start:
            m["_parsed_ts"] = ts
            recent_msgs.append(m)

    msg_ids = [m["id"] for m in recent_msgs]
    classifications: list[dict] = []
    if msg_ids:
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

    # Si filtre site demandé, on restreint les messages aux seuls qui mentionnent
    # un alias du site (dans entities.sites de leur classification). Les messages
    # sans classification sont exclus du périmètre du filtre.
    if site_aliases is not None:
        from app.routes.sites import site_alias_match

        def msg_mentions_site(msg: dict) -> bool:
            c = class_by_msg.get(msg["id"])
            if not c:
                return False
            ents = c.get("entities") or {}
            return site_alias_match(ents.get("sites") or [], site_aliases)

        recent_msgs = [m for m in recent_msgs if msg_mentions_site(m)]

    def in_window(start: datetime, end: datetime, msg: dict) -> bool:
        ts = msg.get("_parsed_ts")
        return ts is not None and start <= ts < end

    current_msgs = [m for m in recent_msgs if in_window(cur_start, cur_end, m)]
    previous_msgs = [m for m in recent_msgs if in_window(prev_start, prev_end, m)]

    def count_cat(msgs: list[dict], cat: str) -> int:
        return sum(1 for m in msgs if class_by_msg.get(m["id"], {}).get("business_category") == cat)

    def count_prio(msgs: list[dict], prio: str) -> int:
        return sum(1 for m in msgs if class_by_msg.get(m["id"], {}).get("priority") == prio)

    def count_action(msgs: list[dict]) -> int:
        return sum(1 for m in msgs if class_by_msg.get(m["id"], {}).get("action_required") is True)

    def kpis_for(msgs: list[dict]) -> dict[str, int]:
        return {
            "messages": len(msgs),
            "incidents": count_cat(msgs, "incident"),
            "urgent": count_prio(msgs, "urgent"),
            "high": count_prio(msgs, "high"),
            "demande_action": count_cat(msgs, "demande_action"),
            "action_required": count_action(msgs),
            "livraisons": count_cat(msgs, "livraison"),
        }

    # Contexte sur les 30 derniers jours avant cur_end (pour catégories/priorités/sites)
    ctx_start = cur_end - timedelta(days=30)
    ctx_msgs = [m for m in recent_msgs if in_window(ctx_start, cur_end, m)]

    cat_counter: Counter[str] = Counter()
    prio_counter: Counter[str] = Counter()
    site_counter: Counter[str] = Counter()
    sender_counter: Counter[str] = Counter()

    for m in ctx_msgs:
        c = class_by_msg.get(m["id"])
        if c:
            if c.get("business_category"):
                cat_counter[c["business_category"]] += 1
            if c.get("priority"):
                prio_counter[c["priority"]] += 1
            entities = c.get("entities") or {}
            for s in entities.get("sites") or []:
                if isinstance(s, str) and s.strip():
                    site_counter[s.strip()] += 1
        sender_counter[
            m.get("sender_display_name") or m.get("sender_phone") or "?"
        ] += 1

    categories_breakdown = [{"category": k, "count": v} for k, v in cat_counter.most_common(10)]
    priorities_breakdown = [
        {"priority": k, "count": v}
        for k, v in sorted(
            prio_counter.items(),
            key=lambda x: ["urgent", "high", "medium", "low"].index(x[0])
            if x[0] in ("urgent", "high", "medium", "low")
            else 99,
        )
    ]
    top_sites = [{"site": k, "count": v} for k, v in site_counter.most_common(10)]
    top_senders = [{"sender": k, "count": v} for k, v in sender_counter.most_common(8)]

    # Urgent items dans la fenêtre courante (pas seulement les 7 jours auparavant)
    urgent_items = []
    for m in current_msgs:
        c = class_by_msg.get(m["id"])
        if not c:
            continue
        if c.get("priority") not in ("urgent", "high"):
            continue
        urgent_items.append(
            {
                "message_id": m["id"],
                "sender": m.get("sender_display_name") or m.get("sender_phone"),
                "sent_at": m["sent_at"],
                "category": c.get("business_category"),
                "priority": c.get("priority"),
                "summary": c.get("summary"),
                "action_required": c.get("action_required"),
                "raw_text": (m.get("raw_text") or "")[:200],
            }
        )
    urgent_items.sort(key=lambda x: x["sent_at"], reverse=True)

    # Nettoyage : on retire le champ technique avant le retour
    for m in recent_msgs:
        m.pop("_parsed_ts", None)

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "period": period,
        "label": label,
        "site_id": site_id,
        "current_window": {
            "start": cur_start.isoformat(),
            "end": cur_end.isoformat(),
        },
        "previous_window": {
            "start": prev_start.isoformat(),
            "end": prev_end.isoformat(),
        },
        "kpis": {
            "current": kpis_for(current_msgs),
            "previous": kpis_for(previous_msgs),
        },
        "categories": categories_breakdown,
        "priorities": priorities_breakdown,
        "top_sites": top_sites,
        "top_senders": top_senders,
        "urgent_items": urgent_items[:30],
    }
