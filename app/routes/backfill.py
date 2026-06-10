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
from datetime import datetime, timezone

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import settings
from app.db import get_supabase
from app.services import import_export, ingest
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

    def ts_utc_key(ts: str | datetime | None) -> str:
        """
        Clé robuste : convertit toujours en UTC, retourne 'YYYY-MM-DDTHH:MM:SS'.
        Gère les offsets variables que Supabase renvoie selon ses settings projet.
        """
        if ts is None:
            return ""
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return ts[:19]
        else:
            dt = ts
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 2. Index (timestamp UTC à la seconde, début du raw_text) → [messages]
    by_key: dict[tuple[str, str], list[dict]] = {}
    for m in existing:
        ts_key = ts_utc_key(m.get("sent_at"))
        txt_key = (m.get("raw_text") or "")[:80].strip()
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

        ts_key = ts_utc_key(parsed["dt"])
        txt_key = (raw_text_eq or "")[:80].strip()

        if len(debug_parsed_keys) < 5:
            debug_parsed_keys.append({
                "ts": ts_key,
                "txt_first_60": txt_key[:60],
                "sender": new_sender,
            })

        candidates = by_key.get((ts_key, txt_key), [])

        if not candidates:
            if len(debug_not_found_samples) < 5:
                # Cherche les keys DB avec ce même timestamp pour comparer textes
                same_ts = [
                    {"db_txt_first_60": k[1][:60], "n": len(v)}
                    for k, v in by_key.items()
                    if k[0] == ts_key
                ]
                debug_not_found_samples.append({
                    "parsed_ts": ts_key,
                    "parsed_txt_first_60": txt_key[:60],
                    "parsed_sender": new_sender,
                    "db_keys_at_same_ts": same_ts[:3],
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
