"""
Import de données MKGT (ERP CKDEV) depuis un export CSV.

Gère la détection automatique des colonnes (le format MKGT varie selon
la configuration de l'entreprise), le parsing des dates/quantités en
format français, et le rattachement aux sites canoniques OpsLens.

Usage :
    stats = await import_csv(file_bytes, filename)
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import time
import unicodedata
from datetime import date, datetime
from typing import Any

from app.config import settings
from app.db import get_supabase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping flexible des colonnes MKGT
# ---------------------------------------------------------------------------

# Pour chaque champ canonique : variantes possibles de l'entête CSV (en
# minuscules normalisées). Le PREMIER hit dans les entêtes du fichier gagne.
_COLUMN_ALIASES: dict[str, list[str]] = {
    "external_ref": [
        "n° bl", "n°bl", "num bl", "numero bl", "numéro bl",
        "bon de livraison", "n° bon de livraison", "bl",
        "n° commande", "num commande", "numero commande",
        "commande", "bon de commande", "n° bon",
        "reference", "référence", "ref", "n° facture",
        "facture", "n° ordre", "ordre de collecte",
    ],
    "operation_date": [
        "date", "date d'enlèvement", "date d enlevement",
        "date enlevement", "date d'intervention", "date intervention",
        "date opération", "date operation", "date de collecte",
        "date collecte", "date réalisation", "date realisation",
        "date d'execution", "date execution", "date prevue", "date prévue",
    ],
    "client_name": [
        "client", "nom client", "raison sociale", "nom du client",
        "donneur d'ordre", "donneur ordre", "commanditaire",
    ],
    "site_name": [
        "chantier", "site", "lieu", "nom chantier", "nom du chantier",
        "adresse chantier", "localisation", "lieu d'enlèvement",
        "lieu enlevement", "point d'enlèvement", "point enlevement",
        "destination", "nom du site", "nom site", "chantier client",
    ],
    "waste_type": [
        "déchet", "dechet", "famille", "matière", "matiere",
        "type déchet", "type dechet", "nature déchet", "nature dechet",
        "nature", "libellé", "libelle", "désignation", "designation",
        "article", "produit", "famille déchet",
    ],
    "container_type": [
        "type benne", "type de benne", "benne", "contenant",
        "type conteneur", "conteneur", "type de contenant",
        "type volume", "type de volume", "materiel",
    ],
    "quantity": [
        "quantité", "quantite", "poids", "tonnage", "masse",
        "qté", "qte", "volume réalisé", "volume realise",
        "poids net", "poids brut", "poids livré",
    ],
    "unit": [
        "unité", "unite", "unité de mesure", "unité de poids",
    ],
    "status": [
        "statut", "état", "etat", "status", "situation",
        "état commande", "statut commande", "état ordre",
    ],
    "driver": [
        "chauffeur", "conducteur", "agent", "nom chauffeur",
        "prenom chauffeur", "prénom chauffeur", "nom du chauffeur",
    ],
    "vehicle": [
        "véhicule", "vehicule", "camion", "engin", "immatriculation",
        "plaque", "n° véhicule", "matricule",
    ],
    "amount_ht": [
        "montant ht", "montant h.t.", "prix ht", "prix h.t.",
        "total ht", "total h.t.", "montant", "prix", "tarif",
        "prix unitaire ht", "prix total ht",
    ],
}

_DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
    "%d/%m/%y", "%d-%m-%y", "%Y/%m/%d",
]

_STATUS_MAP = {
    "réalisé": "réalisé", "realise": "réalisé",
    "facturé": "facturé", "facture": "facturé",
    "planifié": "planifié", "planifie": "planifié",
    "annulé": "annulé", "annule": "annulé",
    "en cours": "en cours",
    "terminé": "réalisé", "termine": "réalisé",
    "validé": "réalisé", "valide": "réalisé",
    "livré": "réalisé", "livre": "réalisé",
    "oui": "réalisé", "non": "planifié",
}


def _normalize(s: str) -> str:
    """Minuscule + strip + supprime accents (pour comparaison souple)."""
    s = s.strip().lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _build_column_map(headers: list[str]) -> dict[str, int]:
    """
    Retourne {champ_canonique: index_colonne} pour les colonnes détectées.
    Cherche par égalité exacte puis par inclusion (dans les deux sens).
    """
    norm_headers = [_normalize(h) for h in headers]
    result: dict[str, int] = {}

    for field, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            norm_alias = _normalize(alias)
            for idx, nh in enumerate(norm_headers):
                if norm_alias == nh or norm_alias in nh or nh in norm_alias:
                    if field not in result:
                        result[field] = idx
                    break
            if field in result:
                break

    return result


def _parse_date(value: str) -> date | None:
    v = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    return None


def _parse_number(value: str) -> float | None:
    """Parse un nombre avec séparateur français (virgule décimale)."""
    v = re.sub(r"[\s ']", "", value.strip())  # espace insécable / apostrophe
    v = v.replace(",", ".")
    v = v.replace("€", "").replace("EUR", "").replace("$", "")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", v)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _normalize_status(value: str) -> str | None:
    norm = _normalize(value)
    return _STATUS_MAP.get(norm, value.strip() or None)


def _file_hash(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()[:16]


def _row_hash(raw_row: dict, batch_id: str) -> str:
    payload = f"{batch_id}|{sorted(raw_row.items())}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:24]


def _site_match(site_name: str, aliases: list[str]) -> bool:
    """Correspondance substring bidirectionnelle, insensible aux accents."""
    if not site_name or not aliases:
        return False
    s_norm = _normalize(site_name)
    for a in aliases:
        a_norm = _normalize(a)
        if a_norm and (a_norm in s_norm or s_norm in a_norm):
            return True
    return False


async def import_csv(
    file_bytes: bytes,
    filename: str,
    *,
    delimiter: str | None = None,
) -> dict[str, Any]:
    """
    Parse et importe un CSV MKGT dans mkgt_operations.
    Retourne des stats détaillées (colonnes détectées, sites matchés, erreurs).
    """
    sb = get_supabase()
    batch_id = _file_hash(file_bytes)
    t0 = time.monotonic()

    # Décodage : essaie UTF-8 BOM d'abord (Excel FR), puis latin-1 / cp1252
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Impossible de décoder le fichier (essayé utf-8, latin-1, cp1252)")

    # Détection du délimiteur
    if delimiter is None:
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ";"  # défaut pour exports Excel FR

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("CSV vide ou sans entêtes")

    headers = list(reader.fieldnames)
    col_map = _build_column_map(headers)
    detected = {field: headers[idx] for field, idx in col_map.items()}

    logger.info(
        "MKGT import batch=%s delimiter=%r detected=%s",
        batch_id, delimiter, list(detected.keys()),
    )

    # Chargement des sites canoniques pour matching
    sites_res = (
        sb.table("sites")
        .select("id,canonical_name,aliases")
        .eq("company_id", settings.company_id)
        .eq("is_active", True)
        .execute()
    )
    canonical_sites = sites_res.data or []

    stats: dict[str, Any] = {
        "filename": filename,
        "batch_id": batch_id,
        "delimiter_detected": delimiter,
        "columns_detected": detected,
        "columns_in_csv": headers,
        "rows_parsed": 0,
        "rows_skipped_empty": 0,
        "rows_inserted": 0,
        "rows_skipped_duplicate": 0,
        "rows_errors": 0,
        "sites_matched": 0,
        "sites_unmatched": [],
        "sample_errors": [],
    }

    unmatched_sites: list[str] = []

    def cell(row: dict, field: str) -> str:
        idx = col_map.get(field)
        if idx is None:
            return ""
        return (row.get(headers[idx]) or "").strip()

    all_rows = list(reader)
    stats["rows_parsed"] = len(all_rows)

    rows_to_insert: list[dict] = []

    for raw_row in all_rows:
        values_nonempty = [v for v in raw_row.values() if v and v.strip()]
        if not values_nonempty:
            stats["rows_skipped_empty"] += 1
            continue

        try:
            external_ref = cell(raw_row, "external_ref") or None
            date_str = cell(raw_row, "operation_date")
            client_name = cell(raw_row, "client_name") or None
            site_name = cell(raw_row, "site_name") or None
            waste_type = cell(raw_row, "waste_type") or None
            container_type = cell(raw_row, "container_type") or None
            qty_str = cell(raw_row, "quantity")
            unit = cell(raw_row, "unit") or None
            status_raw = cell(raw_row, "status")
            driver = cell(raw_row, "driver") or None
            vehicle = cell(raw_row, "vehicle") or None
            amount_str = cell(raw_row, "amount_ht")

            operation_date = _parse_date(date_str) if date_str else None
            quantity = _parse_number(qty_str) if qty_str else None
            amount_ht = _parse_number(amount_str) if amount_str else None
            status = _normalize_status(status_raw) if status_raw else None

            # Matching site canonique
            site_id = None
            if site_name:
                for site in canonical_sites:
                    aliases = (site.get("aliases") or []) + [site["canonical_name"]]
                    if _site_match(site_name, aliases):
                        site_id = site["id"]
                        break
                if site_id:
                    stats["sites_matched"] += 1
                elif site_name not in unmatched_sites:
                    unmatched_sites.append(site_name)

            raw_data = {k: v for k, v in raw_row.items() if k is not None}

            rows_to_insert.append({
                "company_id": settings.company_id,
                "external_ref": external_ref,
                "operation_date": operation_date.isoformat() if operation_date else None,
                "client_name": client_name,
                "site_name": site_name,
                "site_id": site_id,
                "waste_type": waste_type,
                "container_type": container_type,
                "quantity": quantity,
                "unit": unit,
                "status": status,
                "driver": driver,
                "vehicle": vehicle,
                "amount_ht": amount_ht,
                "raw_data": raw_data,
                "import_batch_id": batch_id,
                "row_hash": _row_hash(raw_data, batch_id),
            })

        except Exception as exc:  # noqa: BLE001
            stats["rows_errors"] += 1
            if len(stats["sample_errors"]) < 5:
                stats["sample_errors"].append(f"{type(exc).__name__}: {str(exc)[:150]}")
            logger.exception("MKGT row parse error: %s", exc)

    stats["sites_unmatched"] = unmatched_sites[:20]

    if not rows_to_insert:
        stats["elapsed_seconds"] = round(time.monotonic() - t0, 2)
        return stats

    # Upsert en batch de 100, idempotent sur row_hash
    BATCH = 100
    for i in range(0, len(rows_to_insert), BATCH):
        chunk = rows_to_insert[i : i + BATCH]
        try:
            res = (
                sb.table("mkgt_operations")
                .upsert(chunk, on_conflict="company_id,import_batch_id,row_hash")
                .execute()
            )
            stats["rows_inserted"] += len(res.data or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning("MKGT batch upsert failed (%s), retry row-by-row", exc)
            for row in chunk:
                try:
                    res = (
                        sb.table("mkgt_operations")
                        .upsert([row], on_conflict="company_id,import_batch_id,row_hash")
                        .execute()
                    )
                    if res.data:
                        stats["rows_inserted"] += 1
                    else:
                        stats["rows_skipped_duplicate"] += 1
                except Exception as row_exc:  # noqa: BLE001
                    stats["rows_errors"] += 1
                    if len(stats["sample_errors"]) < 5:
                        stats["sample_errors"].append(
                            f"upsert: {type(row_exc).__name__}: {str(row_exc)[:150]}"
                        )

    stats["elapsed_seconds"] = round(time.monotonic() - t0, 2)
    logger.info(
        "MKGT import terminé: inserted=%s errors=%s sites_matched=%s unmatched=%s",
        stats["rows_inserted"], stats["rows_errors"],
        stats["sites_matched"], len(unmatched_sites),
    )
    return stats
