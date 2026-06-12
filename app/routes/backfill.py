"""
Endpoint admin pour charger l'historique du groupe pilote.

- /admin/backfill        : récupère l'historique récent depuis WAHA
- /admin/reclassify      : (re)classe IA les messages texte existants
- /admin/waha-watchdog   : vérifie/redémarre la session WAHA
- /admin/import-export   : importe un export WhatsApp natif (.txt)

À lancer manuellement (idempotents grâce aux upserts).
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import settings
from app.db import get_supabase
from app.services import import_export, import_export_media, import_mkgt, ingest
from app.services.ai import classify as classify_service
from app.services.groups import get_or_create_pilot_group_id
from app.waha import WahaClient

router = APIRouter(prefix="/admin", tags=["admin"])
logger = logging.getLogger(__name__)


class BackfillRequest(BaseModel):
    limit: int = Field(default=200, ge=1, le=2000)


class ReclassifyRequest(BaseModel):
    limit: int = Field(default=2000, ge=1, le=10000)
    offset: int = Field(default=0, ge=0)
    skip_if_exists: bool = True
    concurrency: int = Field(default=10, ge=1, le=30)


class ReclassifyMissingRequest(BaseModel):
    """Classifie les messages texte qui n'ont AUCUNE classification en base."""
    concurrency: int = Field(default=5, ge=1, le=20)
    max_messages: int = Field(default=10000, ge=1, le=20000)


class AnalyzeMissingImagesRequest(BaseModel):
    """Lance l'analyse vision sur les images sans entrée dans image_analysis."""
    concurrency: int = Field(default=3, ge=1, le=10)
    max_images: int = Field(default=100, ge=1, le=2500)


class ReclassifyImagesVisionRequest(BaseModel):
    """Re-classe les messages image en fusionnant la description vision."""
    concurrency: int = Field(default=4, ge=1, le=15)
    max_messages: int = Field(default=2000, ge=1, le=10000)


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
    Reclasse les messages texte qui n'ont pas encore de classification IA.

    Paramètres :
      - limit (≤ 10000) : nombre max de messages à examiner
      - offset           : pour reprendre une seconde passe sans re-lire les premiers
      - skip_if_exists   : si True (défaut), ne reclasse pas les messages déjà classifiés
      - concurrency      : nombre d'appels Claude parallèles (défaut 10)

    Tri stable par sent_at ASC pour que offset=N pointe toujours sur les mêmes
    messages. Combine bien : limit=2000 offset=0, puis 2000 offset=2000, etc.
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    sb = get_supabase()
    t0 = time.monotonic()

    rows_res = (
        sb.table("whatsapp_messages")
        .select("id,raw_text")
        .order("sent_at", desc=False)
        .limit(body.limit)
        .offset(body.offset)
        .execute()
    )
    rows = rows_res.data or []

    stats = {
        "examined": len(rows),
        "offset": body.offset,
        "limit": body.limit,
        "concurrency": body.concurrency,
        "classified": 0,
        "skipped_empty": 0,
        "skipped_existing": 0,
        "errors": 0,
    }

    # Pré-filtre : exclure les messages sans texte (économise des allers-retours DB)
    work = [r for r in rows if (r.get("raw_text") or "").strip()]
    stats["skipped_empty"] = len(rows) - len(work)

    if not work:
        logger.info("Reclassify : rien à faire pour offset=%d", body.offset)
        stats["elapsed_seconds"] = round(time.monotonic() - t0, 2)
        return stats

    sem = asyncio.Semaphore(body.concurrency)

    async def worker(row: dict) -> str:
        async with sem:
            try:
                result = await classify_service.classify_message(
                    row["id"],
                    row["raw_text"],
                    skip_if_exists=body.skip_if_exists,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Reclassify failed for %s: %s", row["id"], exc)
                return "error"
            return "skipped_existing" if result is None else "classified"

    results = await asyncio.gather(*(worker(r) for r in work))
    for outcome in results:
        stats[outcome] = stats.get(outcome, 0) + 1

    stats["elapsed_seconds"] = round(time.monotonic() - t0, 2)
    logger.info("Reclassify terminé: %s", stats)
    return stats


async def _safety_backfill(limit: int) -> dict:
    """
    Filet de sécurité : récupère les N derniers messages du groupe pilote
    directement via l'API WAHA et les ré-ingère (upsert idempotent).

    Indispensable car le webhook WAHA peut se perdre après un redémarrage de
    session (config par-session non persistée). Ce backfill garantit que les
    messages finissent en base même si le webhook est muet — au pire avec le
    délai du cron watchdog (10 min).

    Quasi gratuit : l'upsert est idempotent et la classification IA est
    skip_if_exists=True, donc seuls les messages réellement nouveaux coûtent
    un appel Claude.
    """
    waha = WahaClient()
    stats = {"received": 0, "stored": 0, "ignored": 0, "errors": 0}
    try:
        raw_messages = await waha.fetch_messages(
            chat_id=settings.pilot_group_id,
            limit=limit,
            download_media=False,
        )
    finally:
        await waha.aclose()

    stats["received"] = len(raw_messages)
    for raw in raw_messages:
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
            logger.exception("Safety backfill item failed: %s", exc)
            stats["errors"] += 1
    return stats


@router.post("/waha-watchdog")
async def waha_watchdog(
    backfill_limit: int = 40,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Watchdog auto-réparateur de l'ingestion WhatsApp. À planifier en cron
    toutes les 10 min. Deux missions :

    1. Vérifie la session WAHA ; la relance si elle n'est pas WORKING.
    2. Filet de sécurité : ré-ingère les derniers messages via l'API WAHA
       (backfill_limit), pour rattraper tout ce que le webhook aurait manqué.

    `backfill_limit=0` désactive le filet de sécurité (juste le check session).
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    waha = WahaClient()
    result: dict[str, Any] = {
        "before_status": "unknown",
        "action": "none",
        "after_status": "unknown",
        "ok": False,
        "safety_backfill": None,
    }

    try:
        info = await waha.get_session_status()
        status = (info or {}).get("status") or "unknown"
        result["before_status"] = status

        if status == "WORKING":
            result["action"] = "none (already WORKING)"
            result["after_status"] = status
            result["ok"] = True
        else:
            # Tentative de relance
            logger.warning("WAHA watchdog: session %s, tentative de redémarrage", status)
            start_r = await waha._client.post(  # noqa: SLF001
                f"/api/sessions/{waha.session_name}/start"
            )
            result["action"] = f"POST start (HTTP {start_r.status_code})"
            await asyncio.sleep(5)
            info2 = await waha.get_session_status()
            after = (info2 or {}).get("status") or "unknown"
            result["after_status"] = after
            result["ok"] = after in ("WORKING", "STARTING", "SCAN_QR_CODE")
            status = after
            info = info2

        # Auto-réparation du webhook : si la session est WORKING mais qu'aucun
        # webhook n'est configuré, on le réinjecte (best-effort, sans restart
        # pour ne pas perturber). C'est la cause racine des arrêts d'ingestion.
        if result.get("after_status") == "WORKING":
            hooks = ((info or {}).get("config") or {}).get("webhooks") or []
            if not hooks:
                logger.warning("WAHA watchdog: 0 webhook → réinjection")
                try:
                    wh = await waha.update_webhook(
                        settings.webhook_callback_url, ["message"], restart=False
                    )
                    result["webhook_repaired"] = wh
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Webhook self-heal failed: %s", exc)
                    result["webhook_repaired"] = {"error": str(exc)}
            else:
                result["webhook_repaired"] = "ok (déjà présent)"
    except Exception as exc:  # noqa: BLE001
        logger.exception("WAHA watchdog session check failed: %s", exc)
        result["action"] = f"error: {exc}"
    finally:
        await waha.aclose()

    # Filet de sécurité : backfill même si la session est WORKING (c'est
    # justement le cas où le webhook peut être muet sans qu'on le voie).
    if backfill_limit > 0 and result.get("after_status") == "WORKING":
        try:
            result["safety_backfill"] = await _safety_backfill(backfill_limit)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Safety backfill failed: %s", exc)
            result["safety_backfill"] = {"error": str(exc)}

    logger.info(
        "WAHA watchdog terminé: status=%s ok=%s backfill=%s",
        result.get("after_status"), result.get("ok"), result.get("safety_backfill"),
    )
    return result


@router.post("/reclassify-missing")
async def reclassify_missing(
    body: ReclassifyMissingRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Classifie uniquement les messages texte SANS classification existante.

    Différent de /reclassify :
    - Fait un anti-join explicite (1 fetch des message_ids classifiés, 1 fetch
      des messages texte, diff en mémoire) au lieu de skip-par-row.
    - Compte sans ambiguïté "newly_classified" vs "errors" (pas de catégorie
      "skipped_existing" trompeuse).
    - Concurrency par défaut à 5 pour limiter le rate-limit Anthropic.

    Idéal pour un rattrapage après un /reclassify partiel.
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    sb = get_supabase()
    t0 = time.monotonic()
    PAGE = 1000

    # 1. Toutes les classifications existantes (paginé par 1000)
    classified_ids: set[str] = set()
    page = 0
    while True:
        res = (
            sb.table("message_classifications")
            .select("message_id")
            .limit(PAGE)
            .offset(page * PAGE)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        classified_ids.update(r["message_id"] for r in rows)
        if len(rows) < PAGE:
            break
        page += 1

    # 2. Tous les messages texte (paginé)
    all_messages: list[dict] = []
    page = 0
    while page * PAGE < body.max_messages:
        res = (
            sb.table("whatsapp_messages")
            .select("id,raw_text")
            .order("sent_at", desc=False)
            .limit(PAGE)
            .offset(page * PAGE)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        all_messages.extend(rows)
        if len(rows) < PAGE:
            break
        page += 1

    # 3. Filtre : pas encore classifié ET texte non vide
    missing = [
        m for m in all_messages
        if m["id"] not in classified_ids
        and (m.get("raw_text") or "").strip()
        and len((m.get("raw_text") or "").strip()) >= 2
    ]

    stats = {
        "classified_existing_total": len(classified_ids),
        "messages_with_text_total": sum(
            1 for m in all_messages
            if (m.get("raw_text") or "").strip()
            and len((m.get("raw_text") or "").strip()) >= 2
        ),
        "missing_at_start": len(missing),
        "concurrency": body.concurrency,
        "newly_classified": 0,
        "errors": 0,
        "error_message_ids": [],
    }

    if not missing:
        stats["elapsed_seconds"] = round(time.monotonic() - t0, 2)
        logger.info("reclassify-missing: nothing to do (%s)", stats)
        return stats

    sem = asyncio.Semaphore(body.concurrency)
    errors: list[str] = []

    async def worker(row: dict) -> str:
        async with sem:
            try:
                result = await classify_service.classify_message(
                    row["id"],
                    row["raw_text"],
                    skip_if_exists=False,  # on a déjà filtré, pas besoin
                )
                if result is None:
                    # classify_message catch les exceptions et retourne None
                    return "error"
                return "classified"
            except Exception as exc:  # noqa: BLE001
                logger.exception("Classification crash for %s: %s", row["id"], exc)
                return "error"

    results = await asyncio.gather(*(worker(r) for r in missing))
    for outcome, row in zip(results, missing, strict=True):
        if outcome == "classified":
            stats["newly_classified"] += 1
        else:
            stats["errors"] += 1
            if len(errors) < 20:
                errors.append(row["id"])

    stats["error_message_ids"] = errors
    stats["elapsed_seconds"] = round(time.monotonic() - t0, 2)
    logger.info("reclassify-missing terminé: %s", {**stats, "error_message_ids": f"{len(errors)} samples"})
    return stats


@router.post("/reclassify-images-vision")
async def reclassify_images_vision(
    body: ReclassifyImagesVisionRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Re-classe les messages IMAGE déjà en base en fusionnant la description de
    la photo (vision) avec la légende.

    Corrige rétroactivement les messages jugés sur le texte seul — typiquement
    « On fait quoi dans ce cas ??? » + photo d'incident, classé à tort
    non_exploitable. Pré-requis : la photo a déjà une analyse vision
    (sinon lancer /admin/analyze-missing-images d'abord).
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    sb = get_supabase()
    t0 = time.monotonic()
    PAGE = 1000

    # 1. Messages de type image
    image_msgs: list[dict] = []
    page = 0
    while page * PAGE < body.max_messages:
        res = (
            sb.table("whatsapp_messages")
            .select("id,raw_text")
            .eq("message_type", "image")
            .order("sent_at", desc=True)
            .limit(PAGE)
            .offset(page * PAGE)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        image_msgs.extend(rows)
        if len(rows) < PAGE:
            break
        page += 1

    stats: dict[str, Any] = {
        "image_messages": len(image_msgs),
        "with_vision": 0,
        "reclassified": 0,
        "errors": 0,
    }
    if not image_msgs:
        stats["elapsed_seconds"] = round(time.monotonic() - t0, 2)
        return stats

    msg_ids = [m["id"] for m in image_msgs]

    # 2. Médias de ces messages (chunké pour ne pas exploser l'URL)
    media_by_msg: dict[str, str] = {}
    media_ids: list[str] = []
    for i in range(0, len(msg_ids), 100):
        chunk = msg_ids[i : i + 100]
        res = (
            sb.table("whatsapp_media")
            .select("id,message_id")
            .in_("message_id", chunk)
            .execute()
        )
        for mr in res.data or []:
            media_by_msg.setdefault(mr["message_id"], mr["id"])
            media_ids.append(mr["id"])

    # 3. Analyses vision de ces médias
    vision_by_media: dict[str, dict] = {}
    for i in range(0, len(media_ids), 100):
        chunk = media_ids[i : i + 100]
        res = (
            sb.table("image_analysis")
            .select("media_id,visual_description,ocr_text,detected_objects,possible_anomaly,anomaly_description,confidence")
            .in_("media_id", chunk)
            .execute()
        )
        for v in res.data or []:
            vision_by_media[v["media_id"]] = v

    # 4. Reconstruit le texte fusionné et re-classe ceux qui ont une vision
    todo: list[tuple[str, str, dict]] = []
    for m in image_msgs:
        media_id = media_by_msg.get(m["id"])
        vision = vision_by_media.get(media_id) if media_id else None
        if not vision:
            continue
        fused = classify_service.build_image_aware_input(m.get("raw_text"), vision)
        if fused:
            todo.append((m["id"], fused, vision))

    stats["with_vision"] = len(todo)
    if not todo:
        stats["elapsed_seconds"] = round(time.monotonic() - t0, 2)
        return stats

    sem = asyncio.Semaphore(body.concurrency)

    async def worker(item: tuple[str, str, dict]) -> str:
        mid, fused, vision = item
        async with sem:
            try:
                r = await classify_service.classify_message(
                    mid, fused, skip_if_exists=False, image_context=vision
                )
                return "ok" if r else "error"
            except Exception as exc:  # noqa: BLE001
                logger.exception("reclassify-images-vision failed for %s: %s", mid, exc)
                return "error"

    results = await asyncio.gather(*(worker(t) for t in todo))
    for r in results:
        if r == "ok":
            stats["reclassified"] += 1
        else:
            stats["errors"] += 1

    stats["elapsed_seconds"] = round(time.monotonic() - t0, 2)
    logger.info("reclassify-images-vision: %s", stats)
    return stats


@router.get("/debug-sent-at-sample")
async def debug_sent_at_sample(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """Retourne 3 sent_at bruts depuis la base, pour debug du matching."""
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")
    sb = get_supabase()
    res = (
        sb.table("whatsapp_messages")
        .select("id,sent_at,raw_text,sender_display_name")
        .order("sent_at", desc=False)
        .limit(3)
        .execute()
    )
    return {"samples": res.data or []}


@router.post("/refresh-senders")
async def refresh_senders(
    file: UploadFile = File(..., description="Export WhatsApp .txt avec carnet d'adresses à jour"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Met à jour les `sender_display_name` des messages existants à partir d'un
    NOUVEL export WhatsApp dont le carnet d'adresses a été actualisé.

    Ne crée AUCUN nouveau message — uniquement des UPDATEs. Le matching se
    fait sur (sent_at à la seconde près, début du raw_text) pour identifier
    le message correspondant en base, indépendamment du changement de nom.

    Note : l'export WhatsApp natif ne contient pas les numéros de téléphone,
    donc `sender_phone` reste inchangé.
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")

    sb = get_supabase()
    t0 = time.monotonic()

    # 1. Charger tous les messages existants (paginé)
    existing: list[dict] = []
    PAGE = 1000
    page = 0
    while page < 20:
        res = (
            sb.table("whatsapp_messages")
            .select("id,sent_at,raw_text,sender_display_name")
            .order("sent_at", desc=False)
            .limit(PAGE)
            .offset(page * PAGE)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        existing.extend(rows)
        if len(rows) < PAGE:
            break
        page += 1

    def ts_utc_dt(ts: str | datetime | None) -> datetime | None:
        """Convertit n'importe quel sent_at en datetime UTC."""
        if ts is None:
            return None
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        else:
            dt = ts
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt

    def fmt_sec(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 2. Index (ts UTC seconde, texte normalisé) → [messages]
    # On normalise le texte avec import_export.normalize_for_match qui strippe
    # les patterns « < pièce jointe : ... > » → résiste au changement de format
    # entre export avec/sans médias.
    by_key: dict[tuple[str, str], list[dict]] = {}
    for m in existing:
        dt = ts_utc_dt(m.get("sent_at"))
        if dt is None:
            continue
        ts_key = fmt_sec(dt)
        txt_key = import_export.normalize_for_match(m.get("raw_text"))
        by_key.setdefault((ts_key, txt_key), []).append(m)

    stats: dict[str, int | float | str] = {
        "filename": file.filename or "",
        "existing_in_db": len(existing),
        "parsed": 0,
        "skipped_system": 0,
        "matched": 0,
        "not_found": 0,
        "no_change": 0,
        "updated": 0,
        "errors": 0,
    }
    sample_changes: list[str] = []
    debug_parsed_keys: list[dict] = []
    debug_not_found_samples: list[dict] = []

    # 3. Re-parser le nouvel export
    for parsed in import_export.parse_export(text):
        stats["parsed"] = int(stats["parsed"]) + 1

        new_sender = import_export._normalize_sender(parsed["sender"])
        if new_sender in {"ADS Multi Sites"}:
            stats["skipped_system"] = int(stats["skipped_system"]) + 1
            continue

        mtype, raw_text_eq = import_export._classify_type(parsed["text"])
        if mtype == "system":
            stats["skipped_system"] = int(stats["skipped_system"]) + 1
            continue

        parsed_dt: datetime = parsed["dt"]
        # Normalise le texte parsé avec la même fonction que pour le DB
        txt_key = import_export.normalize_for_match(raw_text_eq)

        if len(debug_parsed_keys) < 5:
            debug_parsed_keys.append({
                "ts": fmt_sec(parsed_dt.astimezone(timezone.utc)),
                "txt_first_25": txt_key,
                "sender": new_sender,
            })

        # Recherche tolérante : on essaie ts exact, puis ±1, ±2, ±3 secondes
        # (export WhatsApp peut décaler les timestamps d'1 sec entre 2 exports).
        candidates: list[dict] = []
        for delta_sec in (0, -1, 1, -2, 2, -3, 3):
            alt_dt = parsed_dt + timedelta(seconds=delta_sec)
            alt_key = (fmt_sec(alt_dt.astimezone(timezone.utc)), txt_key)
            if alt_key in by_key:
                candidates = by_key[alt_key]
                break

        if not candidates:
            if len(debug_not_found_samples) < 5:
                ts_exact = fmt_sec(parsed_dt.astimezone(timezone.utc))
                # Cherche toutes les keys DB sur ±5 sec autour du ts parsé
                nearby = []
                for d in range(-5, 6):
                    alt = fmt_sec((parsed_dt + timedelta(seconds=d)).astimezone(timezone.utc))
                    for k in by_key:
                        if k[0] == alt:
                            nearby.append({
                                "db_ts": k[0],
                                "delta_sec": d,
                                "db_txt": k[1][:30],
                            })
                debug_not_found_samples.append({
                    "parsed_ts": ts_exact,
                    "parsed_txt": txt_key,
                    "parsed_sender": new_sender,
                    "db_nearby": nearby[:5],
                })
            stats["not_found"] = int(stats["not_found"]) + 1
            continue

        # Si plusieurs candidats à la même clé, on prend celui dont le sender
        # actuel est le plus différent (priorité au "~ qqun" non identifié).
        msg = candidates[0]
        stats["matched"] = int(stats["matched"]) + 1

        current_sender = msg.get("sender_display_name") or ""
        if current_sender == new_sender:
            stats["no_change"] = int(stats["no_change"]) + 1
            continue

        try:
            sb.table("whatsapp_messages").update(
                {"sender_display_name": new_sender}
            ).eq("id", msg["id"]).execute()
            stats["updated"] = int(stats["updated"]) + 1
            if len(sample_changes) < 20:
                sample_changes.append(f"{current_sender!r} → {new_sender!r}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Update sender failed for %s: %s", msg["id"], exc)
            stats["errors"] = int(stats["errors"]) + 1

    stats["elapsed_seconds"] = round(time.monotonic() - t0, 2)
    stats["sample_changes"] = sample_changes  # type: ignore[assignment]
    stats["debug_parsed_keys"] = debug_parsed_keys  # type: ignore[assignment]
    stats["debug_not_found_samples"] = debug_not_found_samples  # type: ignore[assignment]
    logger.info(
        "refresh-senders terminé: matched=%s updated=%s not_found=%s",
        stats["matched"], stats["updated"], stats["not_found"],
    )
    return stats


@router.post("/analyze-missing-images")
async def analyze_missing_images(
    body: AnalyzeMissingImagesRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Rattrape les images stockées dans whatsapp_media mais sans entrée dans
    image_analysis : télécharge l'image depuis Supabase Storage et appelle
    Claude Sonnet vision.

    Sortie : stats détaillées (analyzed / errors / sample_errors).
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    from app.services.ai import vision as vision_service
    import httpx

    sb = get_supabase()
    t0 = time.time()

    # 1. Récupère tous les media_ids déjà analysés
    analyzed_ids: set[str] = set()
    page = 0
    PAGE = 1000
    while page < 20:
        res = (
            sb.table("image_analysis")
            .select("media_id")
            .limit(PAGE)
            .offset(page * PAGE)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        analyzed_ids.update(r["media_id"] for r in rows)
        if len(rows) < PAGE:
            break
        page += 1

    # 2. Récupère les media images stockés
    media_rows: list[dict] = []
    page = 0
    while page < 20 and len(media_rows) < body.max_images * 2:
        res = (
            sb.table("whatsapp_media")
            .select("id,storage_path,mime_type,media_type,status")
            .eq("media_type", "image")
            .eq("status", "stored")
            .order("created_at", desc=False)
            .limit(PAGE)
            .offset(page * PAGE)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        media_rows.extend(rows)
        if len(rows) < PAGE:
            break
        page += 1

    # 3. Filtre ceux pas encore analysés
    todo = [
        m for m in media_rows
        if m["id"] not in analyzed_ids and m.get("storage_path")
    ][: body.max_images]

    sample_errors: list[str] = []
    stats: dict[str, Any] = {
        "total_images_stored": len(media_rows),
        "already_analyzed": len(analyzed_ids),
        "todo": len(todo),
        "concurrency": body.concurrency,
        "analyzed": 0,
        "skipped_unsupported": 0,
        "errors": 0,
        "sample_errors": sample_errors,
    }

    if not todo:
        stats["elapsed_seconds"] = round(time.time() - t0, 2)
        return stats

    # Helper : signed URL pour télécharger depuis Storage
    base = settings.supabase_url.rstrip("/")
    auth_headers = {"apikey": settings.supabase_secret_key, "Authorization": f"Bearer {settings.supabase_secret_key}"}

    async def download_image(storage_path: str) -> tuple[bytes, str] | None:
        # Endpoint privé "object" car le bucket est privé
        url = f"{base}/storage/v1/object/Media/{storage_path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=auth_headers)
            if r.status_code != 200:
                return None
            ct = r.headers.get("content-type") or "image/jpeg"
            return r.content, ct

    sem = asyncio.Semaphore(body.concurrency)

    async def worker(media: dict) -> str:
        async with sem:
            try:
                dl = await download_image(media["storage_path"])
                if dl is None:
                    return "error_download"
                image_bytes, ct = dl
                # Préfère le mime_type stocké si présent, sinon celui du content-type
                mime = media.get("mime_type") or ct
                if mime not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
                    return "unsupported"
                result = await vision_service.analyze_image(
                    media_id=media["id"],
                    image_bytes=image_bytes,
                    mime_type=mime,
                )
                if result is None:
                    return "error_analysis"
                return "ok"
            except Exception as exc:  # noqa: BLE001
                logger.exception("Vision backfill failed for %s: %s", media["id"], exc)
                if len(sample_errors) < 10:
                    sample_errors.append(f"{media['id']}: {type(exc).__name__}: {exc!s}[:200]")
                return "error_exception"

    results = await asyncio.gather(*(worker(m) for m in todo))
    for r in results:
        if r == "ok":
            stats["analyzed"] += 1
        elif r == "unsupported":
            stats["skipped_unsupported"] += 1
        else:
            stats["errors"] += 1

    stats["sample_errors"] = sample_errors
    stats["elapsed_seconds"] = round(time.time() - t0, 2)
    logger.info("analyze-missing-images: %s", stats)
    return stats


@router.post("/import-export-with-media")
async def import_whatsapp_export_with_media(
    file: UploadFile = File(..., description="Zip WhatsApp export AVEC médias"),
    upload_concurrency: int = 3,
    analyze_images: bool = True,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Importe un zip d'export WhatsApp AVEC médias. Parse le _chat.txt, upload
    chaque média référencé dans le bucket Supabase Storage, crée les rows
    whatsapp_media, et lance vision Claude sur les images.

    `upload_concurrency` : nb d'uploads en parallèle (défaut 3 pour ménager
    le rate-limit Anthropic vision).
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, detail="Le fichier doit être un .zip")

    t0 = time.time()
    try:
        stats = await import_export_media.import_zip(
            file.file,
            upload_concurrency=upload_concurrency,
            analyze_images=analyze_images,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("import-export-with-media failed: %s", exc)
        raise HTTPException(500, detail=f"Import zip échoué : {exc}")

    stats["elapsed_seconds"] = round(time.time() - t0, 2)
    stats["filename"] = file.filename
    logger.info("import-export-with-media: %s", stats)
    return stats


@router.get("/diagnose-pipeline")
async def diagnose_pipeline(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Diagnostic complet du pipeline d'ingestion WhatsApp → OpsLens.

    Répond à : "pourquoi les messages ne se mettent plus à jour ?"
    en vérifiant chaque maillon de la chaîne :
      1. Session WAHA : statut + URL(s) webhook configurée(s)
      2. raw_webhooks : WAHA envoie-t-il encore des events ? (24h glissantes)
      3. whatsapp_messages : date du dernier message ingéré

    Le maillon cassé saute aux yeux :
      - webhook absent/mauvaise URL → WAHA connecté mais ne transmet pas
      - raw_webhooks vide récent → WAHA n'appelle plus le backend
      - raw_webhooks OK mais messages vieux → souci de filtrage/normalisation
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    sb = get_supabase()
    now = datetime.now(tz=timezone.utc)

    diag: dict[str, Any] = {"checked_at": now.isoformat()}

    # 1. Session WAHA + config webhook
    waha = WahaClient()
    try:
        info = await waha.get_session_status()
        config = (info or {}).get("config") or {}
        webhooks = config.get("webhooks") or []
        diag["waha"] = {
            "status": (info or {}).get("status"),
            "webhooks_configured": [
                {
                    "url": w.get("url"),
                    "events": w.get("events"),
                }
                for w in webhooks
                if isinstance(w, dict)
            ],
            "webhook_count": len(webhooks),
        }
    except Exception as exc:  # noqa: BLE001
        diag["waha"] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        await waha.aclose()

    # 2. raw_webhooks : WAHA appelle-t-il encore le backend ?
    # Schéma créé à la main dans Supabase → on ne suppose pas le nom de la
    # colonne date. On découvre les colonnes, puis on trie sur la 1re colonne
    # temporelle trouvée (sinon sur id) pour remonter la dernière entrée.
    try:
        probe = sb.table("raw_webhooks").select("*").limit(1).execute()
        cols = list((probe.data or [{}])[0].keys()) if probe.data else []
        time_col = next(
            (c for c in cols if c in (
                "received_at", "created_at", "inserted_at", "ts", "timestamp"
            )),
            None,
        )
        order_col = time_col or ("id" if "id" in cols else None)
        latest = None
        if order_col:
            latest_res = (
                sb.table("raw_webhooks")
                .select("*")
                .order(order_col, desc=True)
                .limit(1)
                .execute()
            )
            row = (latest_res.data or [{}])[0]
            latest = row.get(time_col) if time_col else f"id={row.get('id')}"
        diag["raw_webhooks"] = {
            "columns": cols,
            "time_column": time_col,
            "latest_entry": latest,
            "has_rows": bool(probe.data),
        }
    except Exception as exc:  # noqa: BLE001
        diag["raw_webhooks"] = {"error": f"{type(exc).__name__}: {exc}"}

    # 3. Dernier message ingéré
    try:
        latest_msg = (
            sb.table("whatsapp_messages")
            .select("sent_at,sender_display_name,message_type")
            .order("sent_at", desc=True)
            .limit(1)
            .execute()
        )
        msg = (latest_msg.data or [{}])[0]
        diag["last_message"] = {
            "sent_at": msg.get("sent_at"),
            "sender": msg.get("sender_display_name"),
            "type": msg.get("message_type"),
        }
    except Exception as exc:  # noqa: BLE001
        diag["last_message"] = {"error": f"{type(exc).__name__}: {exc}"}

    # 4. Verdict automatique
    verdict = "unknown"
    waha_block = diag.get("waha", {})
    if isinstance(waha_block, dict) and waha_block.get("status") != "WORKING":
        verdict = "Session WAHA non connectée → relancer / re-scanner le QR"
    elif isinstance(waha_block, dict) and waha_block.get("webhook_count") == 0:
        verdict = (
            "Session WORKING mais AUCUN webhook configuré → WAHA ne transmet "
            "rien. Reconfigurer le webhook (POST /admin/ensure-webhook)."
        )
    else:
        verdict = (
            "Session WORKING + webhook configuré. La livraison temps réel se "
            "confirme à l'arrivée du prochain message (cf. last_message)."
        )
    diag["verdict"] = verdict

    logger.info("diagnose-pipeline: verdict=%s", verdict)
    return diag


@router.post("/ensure-webhook")
async def ensure_webhook(
    restart: bool = True,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    (Ré)enregistre le webhook WAHA → backend, pour restaurer la livraison
    temps réel des messages.

    Corrige la cause racine du « les messages ne se mettent plus à jour » :
    après un redémarrage de session, WAHA perd son webhook (config par-session).
    Cet endpoint le réinjecte via l'API WAHA.

    `restart=true` (défaut) redémarre la session pour appliquer la config.
    La session étant authentifiée, AUCUN re-scan QR n'est nécessaire.
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    callback = settings.webhook_callback_url
    waha = WahaClient()
    out: dict[str, Any] = {"callback_url": callback, "restart": restart}
    try:
        before = await waha.get_session_status()
        out["webhooks_before"] = ((before or {}).get("config") or {}).get("webhooks") or []
        out["update_result"] = await waha.update_webhook(
            callback, ["message"], restart=restart
        )
        await asyncio.sleep(4)
        after = await waha.get_session_status()
        out["status_after"] = (after or {}).get("status")
        out["webhooks_after"] = ((after or {}).get("config") or {}).get("webhooks") or []
        out["ok"] = len(out["webhooks_after"]) > 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("ensure-webhook failed: %s", exc)
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["ok"] = False
    finally:
        await waha.aclose()

    logger.info("ensure-webhook: ok=%s after=%s", out.get("ok"), out.get("webhooks_after"))
    return out


@router.post("/import-mkgt-csv")
async def import_mkgt_csv(
    file: UploadFile = File(..., description="Export CSV MKGT (séparateur ; ou ,)"),
    delimiter: str | None = None,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Importe un export CSV MKGT dans la table mkgt_operations.

    Détecte automatiquement les colonnes (le format MKGT varie selon la
    configuration de l'entreprise). Idempotent : réimporter le même fichier
    ne crée pas de doublons (upsert sur hash de ligne + batch_id du fichier).

    Retourne : colonnes détectées, stats lignes, sites non matchés.
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, detail="Le fichier doit être un .csv")

    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, detail="Fichier trop grand (max 10 Mo)")

    try:
        stats = await import_mkgt.import_csv(raw, file.filename, delimiter=delimiter)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("import-mkgt-csv failed: %s", exc)
        raise HTTPException(500, detail=f"Import MKGT échoué : {exc}")

    return stats


@router.post("/import-export")
async def import_whatsapp_export(
    file: UploadFile = File(..., description="Fichier _chat.txt export WhatsApp"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Importe un export WhatsApp natif (.txt) dans whatsapp_messages.
    Idempotent : réimporter le même fichier ne crée pas de doublons (upsert
    sur external_message_id déterministe).

    Le fichier est fourni en multipart/form-data sous la clé `file`.
    """
    if settings.waha_webhook_secret:
        if x_admin_token != settings.waha_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid admin token")

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Certains exports sont en UTF-8 BOM ou latin-1 selon le téléphone
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")

    sb = get_supabase()
    group_uuid = get_or_create_pilot_group_id()

    stats = {
        "filename": file.filename,
        "size_bytes": len(raw),
        "parsed": 0,
        "skipped_system": 0,
        "rows_to_store": 0,
        "stored": 0,
        "errors": 0,
    }

    # Dédoublonnage global : un même external_message_id ne doit apparaître
    # qu'une seule fois dans le batch envoyé à PostgREST (sinon erreur
    # "ON CONFLICT DO UPDATE command cannot affect row a second time").
    # On garde la première occurrence.
    seen_ids: set[str] = set()
    rows: list[dict] = []
    duplicates_in_export = 0
    for parsed in import_export.parse_export(text):
        stats["parsed"] += 1
        row = import_export.to_db_row(
            parsed,
            company_id=settings.company_id,
            group_uuid=group_uuid,
        )
        if row is None:
            stats["skipped_system"] += 1
            continue
        mid = row["external_message_id"]
        if mid in seen_ids:
            duplicates_in_export += 1
            continue
        seen_ids.add(mid)
        rows.append(row)

    stats["duplicates_in_export"] = duplicates_in_export
    stats["rows_to_store"] = len(rows)

    # Upsert en batch de 100. Si un batch entier échoue (ex. payload trop gros,
    # ligne avec caractère invalide…), on retombe sur un upsert ligne par
    # ligne pour identifier précisément ce qui pose problème et stocker tout
    # le reste.
    BATCH_SIZE = 100
    sample_errors: list[str] = []
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        try:
            res = (
                sb.table("whatsapp_messages")
                .upsert(
                    batch,
                    on_conflict="company_id,group_id,external_message_id",
                )
                .execute()
            )
            stats["stored"] += len(res.data or [])
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Batch %d failed (%s), retrying row by row",
                i // BATCH_SIZE, exc,
            )

        # Fallback : upsert ligne par ligne pour ne pas perdre tout le batch
        for row in batch:
            try:
                res = (
                    sb.table("whatsapp_messages")
                    .upsert(
                        [row],
                        on_conflict="company_id,group_id,external_message_id",
                    )
                    .execute()
                )
                stats["stored"] += len(res.data or [])
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                if len(sample_errors) < 5:
                    sample_errors.append(
                        f"{row['external_message_id']}: {type(exc).__name__}: {exc!s}[:200]"
                    )
                logger.exception(
                    "Row upsert failed for %s: %s",
                    row["external_message_id"], exc,
                )

    stats["sample_errors"] = sample_errors
    logger.info("Import export terminé: %s", stats)
    return stats
