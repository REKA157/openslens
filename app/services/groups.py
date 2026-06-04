"""
Helpers de résolution de groupes WhatsApp côté DB.
On a besoin de l'UUID interne (table whatsapp_groups) à partir du
chat_id externe (ex: 120363142540472721@g.us) à chaque insertion.
"""

import logging
from functools import lru_cache

from app.config import settings
from app.db import get_supabase

logger = logging.getLogger(__name__)


@lru_cache(maxsize=64)
def get_or_create_pilot_group_id() -> str:
    """
    Récupère (ou crée si absent) le UUID du groupe pilote dans whatsapp_groups.
    Cache en mémoire pour éviter une requête DB à chaque message.
    """
    sb = get_supabase()
    existing = (
        sb.table("whatsapp_groups")
        .select("id")
        .eq("company_id", settings.company_id)
        .eq("external_group_id", settings.pilot_group_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]["id"]

    inserted = (
        sb.table("whatsapp_groups")
        .insert(
            {
                "company_id": settings.company_id,
                "source": "waha",
                "external_group_id": settings.pilot_group_id,
                "display_name": "ADS Multi Sites",
                "is_active": True,
            }
        )
        .execute()
    )
    group_id = inserted.data[0]["id"]
    logger.info("Created whatsapp_groups row for pilot group %s -> %s",
                settings.pilot_group_id, group_id)
    return group_id
