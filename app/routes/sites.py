"""
Endpoints pour la gestion des sites canoniques ADS.

- POST /admin/discover-sites : Claude propose un regroupement (lecture seule)
- POST /admin/save-sites     : enregistre la liste validée par l'humain
- GET  /api/sites            : liste les sites + compteur de messages
- DELETE /api/sites/{id}     : retire un site (utile lors des ajustements)

Le compteur de messages se base sur les classifications dont entities.sites
contient l'un des aliases du site.
"""

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.db import get_supabase
from app.services.ai import sites_discovery

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["admin"])
api_router = APIRouter(prefix="/api/sites", tags=["sites"])


def _check_admin(token: str | None) -> None:
    if settings.waha_webhook_secret and token != settings.waha_webhook_secret:
        raise HTTPException(status_code=401, detail="invalid admin token")


# ----------------------------------------------------------------------------
# /admin/discover-sites
# ----------------------------------------------------------------------------


class DiscoverSitesRequest(BaseModel):
    min_occurrences: int = Field(
        default=2,
        ge=1,
        description="Ignore les noms apparaissant moins de N fois (réduit le bruit)",
    )
    extra_context: str | None = Field(
        default=None,
        description="Optionnel : note libre à passer à Claude (liste officielle des sites ADS)",
    )


@admin_router.post("/discover-sites")
async def discover_sites_endpoint(
    body: DiscoverSitesRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Aggrège les sites extraits dans toutes les classifications, fait tourner
    Claude Sonnet pour proposer un regroupement canonique.
    """
    _check_admin(x_admin_token)
    sb = get_supabase()

    # On lit toutes les classifs (paginé). Pour ~3000 classifs c'est OK.
    classifications: list[dict] = []
    PAGE = 1000
    page = 0
    while page < 20:  # max 20k classifs, marge confortable
        res = (
            sb.table("message_classifications")
            .select("entities")
            .limit(PAGE)
            .offset(page * PAGE)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        classifications.extend(rows)
        if len(rows) < PAGE:
            break
        page += 1

    counts = sites_discovery.aggregate_sites_from_classifications(classifications)
    # Filtre min_occurrences
    filtered = {name: cnt for name, cnt in counts.items() if cnt >= body.min_occurrences}

    if not filtered:
        return {
            "classifications_scanned": len(classifications),
            "raw_distinct": len(counts),
            "after_filter": 0,
            "proposal": {"sites": [], "noise": [], "uncertain": []},
        }

    try:
        proposal = await sites_discovery.discover_sites(
            filtered,
            extra_context=body.extra_context,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("discover-sites Claude call failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Claude failed: {exc}")

    return {
        "classifications_scanned": len(classifications),
        "raw_distinct": len(counts),
        "after_filter": len(filtered),
        "proposal": proposal,
    }


# ----------------------------------------------------------------------------
# /admin/save-sites
# ----------------------------------------------------------------------------


class SiteInput(BaseModel):
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    region: str | None = None
    notes: str | None = None
    is_active: bool = True


class SaveSitesRequest(BaseModel):
    sites: list[SiteInput]
    replace_all: bool = Field(
        default=False,
        description="Si True, supprime tous les sites existants avant d'insérer (reset complet)",
    )


@admin_router.post("/save-sites")
async def save_sites_endpoint(
    body: SaveSitesRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    """
    Sauvegarde la liste canonique de sites. Idempotent : upsert sur
    (company_id, canonical_name). Si replace_all=True, suppression totale d'abord.
    """
    _check_admin(x_admin_token)
    sb = get_supabase()

    stats = {"received": len(body.sites), "deleted_before": 0, "upserted": 0}

    if body.replace_all:
        deleted = (
            sb.table("sites")
            .delete()
            .eq("company_id", settings.company_id)
            .execute()
        )
        stats["deleted_before"] = len(deleted.data or [])

    rows = [
        {
            "company_id": settings.company_id,
            "canonical_name": s.canonical_name.strip(),
            "aliases": [a.strip() for a in s.aliases if a and a.strip()],
            "region": (s.region or "").strip() or None,
            "notes": (s.notes or "").strip() or None,
            "is_active": s.is_active,
        }
        for s in body.sites
        if s.canonical_name.strip()
    ]

    if rows:
        res = (
            sb.table("sites")
            .upsert(rows, on_conflict="company_id,canonical_name")
            .execute()
        )
        stats["upserted"] = len(res.data or [])

    logger.info("save-sites: %s", stats)
    return stats


# ----------------------------------------------------------------------------
# GET /api/sites
# ----------------------------------------------------------------------------


def site_alias_match(entities_sites: list, aliases: list[str]) -> bool:
    """
    True si l'un des aliases du site match (substring case-insensitive,
    dans un sens ou dans l'autre) au moins une des sites extraites de la
    classification.

    Cette fonction est partagée entre /api/sites (compteur) et le filtrage
    site dans le dashboard / les rapports.
    """
    if not aliases or not entities_sites:
        return False
    normalized_aliases = [a.lower() for a in aliases if a]
    for s in entities_sites:
        if not isinstance(s, str):
            continue
        s_norm = s.lower()
        for a_norm in normalized_aliases:
            if a_norm and (a_norm in s_norm or s_norm in a_norm):
                return True
    return False


def fetch_site_aliases(site_id: str) -> list[str] | None:
    """Récupère les aliases d'un site par id. None si introuvable."""
    sb = get_supabase()
    res = (
        sb.table("sites")
        .select("aliases")
        .eq("id", site_id)
        .eq("company_id", settings.company_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0].get("aliases") or []


@api_router.get("/{site_id}")
async def get_site(site_id: str):
    """Récupère un site par son id (pour la page de détail)."""
    sb = get_supabase()
    res = (
        sb.table("sites")
        .select("*")
        .eq("id", site_id)
        .eq("company_id", settings.company_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Site introuvable")
    return res.data[0]


@api_router.get("")
async def list_sites():
    """
    Liste tous les sites avec leur nombre de messages associés (count via
    classifications.entities.sites contains any alias).

    Le compteur est calculé côté Python (PostgREST ne fait pas de JOIN avec un
    array contains complexe efficacement) — OK pour ~50 sites × ~3000 classifs.
    """
    sb = get_supabase()

    sites_res = (
        sb.table("sites")
        .select("*")
        .eq("company_id", settings.company_id)
        .eq("is_active", True)
        .order("canonical_name")
        .execute()
    )
    sites = sites_res.data or []

    # On charge toutes les classifs en mémoire (paginé)
    classifications: list[dict] = []
    PAGE = 1000
    page = 0
    while page < 20:
        res = (
            sb.table("message_classifications")
            .select("entities")
            .limit(PAGE)
            .offset(page * PAGE)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        classifications.extend(rows)
        if len(rows) < PAGE:
            break
        page += 1

    # Pour chaque site, compte le nombre de classifs qui mentionnent un alias
    enriched: list[dict[str, Any]] = []
    for site in sites:
        aliases = site.get("aliases") or []
        count = 0
        for c in classifications:
            entities = c.get("entities") or {}
            ents_sites = entities.get("sites") or []
            if site_alias_match(ents_sites, aliases):
                count += 1
        enriched.append({**site, "message_count": count})

    return {
        "sites": enriched,
        "total": len(enriched),
        "classifications_scanned": len(classifications),
    }
