"""
Webhook entrant depuis WAHA.
WAHA enverra POST /ingest/webhook/waha à chaque événement.
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import settings
from app.services import ingest

router = APIRouter(prefix="/ingest", tags=["ingest"])
logger = logging.getLogger(__name__)


@router.post("/webhook/waha")
async def waha_webhook(
    request: Request,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
):
    # Optionnel : vérification d'un secret partagé pour bloquer les appels non autorisés
    if settings.waha_webhook_secret:
        if x_webhook_secret != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")

    payload = await request.json()
    event = payload.get("event")
    logger.info("Webhook received: event=%s session=%s",
                event, payload.get("session"))

    try:
        result = await ingest.handle_waha_event(payload)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Webhook handling failed: %s", exc)
        # On répond 200 quand même pour que WAHA ne retry pas indéfiniment.
        # L'audit est dans raw_webhooks, on pourra rejouer manuellement.
        return {"status": "error", "detail": "logged for replay"}

    return result
