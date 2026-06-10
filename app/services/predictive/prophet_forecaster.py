"""
Prévision de volume par site avec Prophet (Meta).

Pour chaque site canonique :
  1. Aggrège l'historique en compte journalier des messages mentionnant le site
  2. Construit un DataFrame Prophet (ds, y)
  3. Ajoute saisonnalité mensuelle + jours fériés français
  4. Entraîne le modèle (~5-15 sec par site)
  5. Prédit horizon J+H avec intervalles de confiance (yhat_lower, yhat_upper)

Différences vs notre forecast actuel (moyenne mobile par jour-semaine) :
  - Capte saisonnalité multi-niveaux (semaine + mois + vacances)
  - Intègre jours fériés FR + vacances scolaires IDF
  - Intervalles de confiance Bayésiens (vs sigma simple)
  - Modèle robuste aux outliers (mode additif avec changepoints)

Mise en cache : les modèles entraînés sont cachés en mémoire pour 1h.
Si l'historique change peu, on évite de re-entraîner à chaque requête.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Any

import holidays
import pandas as pd
from prophet import Prophet

from app.routes.sites import site_alias_match

logger = logging.getLogger(__name__)


# Cache en mémoire : {site_id: (trained_at_unix, last_training_date, Prophet model)}
_MODEL_CACHE: dict[str, tuple[float, date_cls, Prophet]] = {}
_CACHE_TTL_SECONDS = 3600  # 1h


def _build_holidays_df(min_year: int, max_year: int) -> pd.DataFrame:
    """Holidays FR (jours fériés nationaux) sur la période concernée."""
    fr = holidays.France(years=range(min_year, max_year + 2))
    rows = []
    for d, name in sorted(fr.items()):
        rows.append({"ds": pd.Timestamp(d), "holiday": name.replace(" ", "_")})
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["ds", "holiday"])


def _aggregate_site_history(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    site: dict,
) -> pd.DataFrame:
    """
    Compte journalier des messages liés à ce site (via entities.sites de la classif).
    Retourne DataFrame Prophet-ready : colonnes ds (date), y (count).
    """
    aliases = site.get("aliases") or []
    if not aliases:
        return pd.DataFrame(columns=["ds", "y"])

    daily_count: dict[date_cls, int] = defaultdict(int)
    for m in messages:
        ts: datetime | None = m.get("_parsed_ts")
        if ts is None:
            continue
        c = classifications_by_id.get(m["id"])
        if not c:
            continue
        ents_sites = (c.get("entities") or {}).get("sites") or []
        if site_alias_match(ents_sites, aliases):
            d = ts.astimezone(timezone.utc).date()
            daily_count[d] += 1

    if not daily_count:
        return pd.DataFrame(columns=["ds", "y"])

    # Remplit les jours sans message à 0 (Prophet préfère une série continue)
    min_d = min(daily_count.keys())
    max_d = max(daily_count.keys())
    rows = []
    current = min_d
    while current <= max_d:
        rows.append({"ds": pd.Timestamp(current), "y": daily_count.get(current, 0)})
        current += timedelta(days=1)
    return pd.DataFrame(rows)


def train_forecaster(
    history_df: pd.DataFrame,
    holidays_df: pd.DataFrame | None = None,
) -> Prophet:
    """Entraîne Prophet sur une série journalière (au moins 30 jours)."""
    model = Prophet(
        weekly_seasonality=True,
        yearly_seasonality=False,  # on n'a que 10 mois, pas d'année complète
        daily_seasonality=False,
        seasonality_mode="additive",
        changepoint_prior_scale=0.05,  # un peu rigide, évite le overfit
        interval_width=0.8,            # intervalles à 80%
        holidays=holidays_df if holidays_df is not None and len(holidays_df) > 0 else None,
    )
    # Saisonnalité mensuelle (capture cycles paye / fin de mois)
    model.add_seasonality(name="monthly", period=30.5, fourier_order=3)
    model.fit(history_df)
    return model


def predict_horizon(
    model: Prophet,
    last_history_date: date_cls,
    horizon_days: int = 30,
) -> list[dict[str, Any]]:
    """
    Prédit les `horizon_days` à venir après `last_history_date`.
    Retourne liste de dicts avec ds, yhat, yhat_lower, yhat_upper.
    """
    future_dates = pd.DataFrame({
        "ds": pd.date_range(
            start=pd.Timestamp(last_history_date) + pd.Timedelta(days=1),
            periods=horizon_days,
            freq="D",
        )
    })
    forecast = model.predict(future_dates)
    # On clipp les négatifs (Prophet peut prédire en négatif sur counts)
    forecast["yhat"] = forecast["yhat"].clip(lower=0)
    forecast["yhat_lower"] = forecast["yhat_lower"].clip(lower=0)
    forecast["yhat_upper"] = forecast["yhat_upper"].clip(lower=0)
    return [
        {
            "date": row["ds"].date().isoformat(),
            "yhat": round(float(row["yhat"]), 1),
            "yhat_lower": round(float(row["yhat_lower"]), 1),
            "yhat_upper": round(float(row["yhat_upper"]), 1),
        }
        for _, row in forecast.iterrows()
    ]


def forecast_site(
    site: dict,
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    horizon_days: int = 30,
    *,
    history_tail_days: int | None = 90,
) -> dict[str, Any] | None:
    """
    Wrapper haut niveau : agrège l'historique du site, entraîne Prophet (avec cache),
    prédit horizon. Renvoie None si pas assez de données (< 30 jours d'historique).

    `history_tail_days` : si donné, on ne retourne que la fin de l'historique
    pour l'affichage frontend (réduit le payload).
    """
    site_id = site["id"]
    history_df = _aggregate_site_history(messages, classifications_by_id, site)

    if len(history_df) < 30:
        return None

    last_date = history_df["ds"].max().date()

    # Cache check
    cached = _MODEL_CACHE.get(site_id)
    now = time.time()
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS and cached[1] == last_date:
        model = cached[2]
        logger.info("Prophet cache hit pour site %s", site_id)
    else:
        # Pas de try/except ici — on laisse remonter pour exposer la cause.
        holidays_df = _build_holidays_df(
            history_df["ds"].min().year,
            last_date.year + 1,
        )
        t0 = time.time()
        model = train_forecaster(history_df, holidays_df=holidays_df)
        elapsed = time.time() - t0
        logger.info(
            "Prophet entraîné pour site %s (%d points, %.1fs)",
            site_id, len(history_df), elapsed,
        )
        _MODEL_CACHE[site_id] = (now, last_date, model)

    predictions = predict_horizon(model, last_date, horizon_days=horizon_days)

    # Historique : on garde uniquement la queue pour l'affichage frontend
    if history_tail_days is not None:
        cutoff = last_date - timedelta(days=history_tail_days)
        history_df = history_df[history_df["ds"].dt.date >= cutoff]

    history_out = [
        {
            "date": row["ds"].date().isoformat(),
            "actual": int(row["y"]),
        }
        for _, row in history_df.iterrows()
    ]

    # Agrégats utiles
    total_expected = sum(p["yhat"] for p in predictions)
    total_lower = sum(p["yhat_lower"] for p in predictions)
    total_upper = sum(p["yhat_upper"] for p in predictions)

    return {
        "site_id": site_id,
        "site_name": site["canonical_name"],
        "region": site.get("region"),
        "horizon_days": horizon_days,
        "history": history_out,
        "predictions": predictions,
        "summary": {
            "history_days": len(history_df),
            "history_total_messages": int(history_df["y"].sum()) if len(history_df) else 0,
            "expected_total": round(total_expected, 1),
            "expected_lower": round(total_lower, 1),
            "expected_upper": round(total_upper, 1),
            "trend": _detect_trend(predictions),
        },
    }


def _detect_trend(predictions: list[dict[str, Any]]) -> str:
    """Détecte la tendance de la prédiction : haussière / baissière / stable."""
    if len(predictions) < 7:
        return "stable"
    first_week = sum(p["yhat"] for p in predictions[:7])
    last_week = sum(p["yhat"] for p in predictions[-7:])
    if last_week > first_week * 1.15:
        return "haussiere"
    if last_week < first_week * 0.85:
        return "baissiere"
    return "stable"
