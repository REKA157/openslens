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
from app.db import get_supabase
from app.services import ingest
from app.services.ai import classify as classify_service
from app.waha import WahaClient

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


class BackfillRequest(BaseModel):
    limit: int = Field(default=200, ge=1, le=2000)


class ReclassifyRequest(BaseModel):
    limit: int = Field(default=200, ge=1, le=2000)
    skip_if_exists: bool = True


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


@router.post("/reclassify")
async def reclassify(
    body: ReclassifyRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Reclasse les anciens messages texte qui n'ont pas encore de
    classification IA. Utile pour rattraper l'historique après avoir
    activé le pipeline IA.
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    sb = get_supabase()

    rows = (
        sb.table("whatsapp_messages")
        .select("id,raw_text")
        .order("ingested_at", desc=True)
        .limit(body.limit)
        .execute()
    )

    stats = {"total": len(rows.data), "classified": 0, "skipped": 0, "errors": 0}

    for row in rows.data:
        text = row.get("raw_text") or ""
        if not text.strip():
            stats["skipped"] += 1
            continue
        try:
            result = await classify_service.classify_message(
                row["id"], text, skip_if_exists=body.skip_if_exists
            )
            if result is None:
                stats["skipped"] += 1
            else:
                stats["classified"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception("Reclassify failed for %s: %s", row["id"], exc)
            stats["errors"] += 1

    logger.info("Reclassify terminé: %s", stats)
    return stats


@router.post("/waha-watchdog")
async def waha_watchdog(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Vérifie le statut de la session WAHA. Si elle n'est pas WORKING,
    tente de la relancer via POST /api/sessions/{session}/start.
    À planifier en cron toutes les 10 min.
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    waha = WahaClient()
    result: dict[str, str | bool] = {
        "before_status": "unknown",
        "action": "none",
        "after_status": "unknown",
        "ok": False,
    }

    try:
        info = await waha.get_session_status()
        status = (info or {}).get("status") or "unknown"
        result["before_status"] = status

        if status == "WORKING":
            result["action"] = "none (already WORKING)"
            result["after_status"] = status
            result["ok"] = True
            logger.info("WAHA watchdog: session WORKING, rien à faire")
            return result

        # Tentative de relance
        logger.warning("WAHA watchdog: session %s, tentative de redémarrage", status)
        # POST /api/sessions/{session}/start sans body
        start_r = await waha._client.post(  # noqa: SLF001
            f"/api/sessions/{waha.session_name}/start"
        )
        result["action"] = f"POST start (HTTP {start_r.status_code})"

        # On lit le nouveau statut après ~5s
        import asyncio
        await asyncio.sleep(5)
        info2 = await waha.get_session_status()
        after = (info2 or {}).get("status") or "unknown"
        result["after_status"] = after
        result["ok"] = after in ("WORKING", "STARTING", "SCAN_QR_CODE")

        logger.info(
            "WAHA watchdog terminé: before=%s after=%s ok=%s",
            status, after, result["ok"],
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("WAHA watchdog failed: %s", exc)
        result["action"] = f"error: {exc}"
        return result
    finally:
        await waha.aclose()
