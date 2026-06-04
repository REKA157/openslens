"""
Endpoint admin pour charger l'historique du groupe pilote depuis WAHA.

Usage :
  curl -X POST https://api.opslens.../admin/backfill \
       -H "X-Admin-Token: <token>" \
       -H "Content-Type: application/json" \
       -d '{"limit": 500}'

À lancer une seule fois (idempotent grâce à l'upsert).
"""

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services import ingest
from app.waha import WahaClient

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


class BackfillRequest(BaseModel):
    limit: int = Field(default=200, ge=1, le=2000)


@router.post("/backfill")
async def backfill(
    body: BackfillRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    # Sécurité : on réutilise le secret webhook comme jeton admin tant qu'on
    # n'a pas un vrai système d'auth. Si non configuré, on autorise (dev).
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    waha = WahaClient()
    try:
        raw_messages = await waha.fetch_messages(
            chat_id=settings.pilot_group_id,
            limit=body.limit,
            download_media=False,
        )
    finally:
        await waha.aclose()

    stats = {"received": len(raw_messages), "stored": 0, "ignored": 0, "errors": 0}

    for raw in raw_messages:
        # On fabrique un faux événement webhook pour réutiliser le pipeline
        synthetic_event = {
            "event": "message",
            "session": settings.waha_session_name,
            "payload": raw,
        }
        try:
            result = await ingest.handle_waha_event(synthetic_event)
            status = result.get("status")
            if status == "stored":
                stats["stored"] += 1
            elif status == "ignored":
                stats["ignored"] += 1
            else:
                stats["errors"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("Backfill item failed: %s", exc)
            stats["errors"] += 1

    logger.info("Backfill terminé: %s", stats)
    return stats
