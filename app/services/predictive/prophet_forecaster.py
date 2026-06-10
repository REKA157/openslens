"""
Prévision de volume par site avec SARIMAX (statsmodels).

SARIMAX = Seasonal AutoRegressive Integrated Moving Average with eXogenous variables.
Modèle de séries temporelles classique, statistiquement fondé, sans compilation
native (contrairement à Prophet qui requiert CmdStan).

Paramètres choisis :
  - order=(1,1,1) : AR(1) sur série stationnarisée (1 différenciation) + MA(1)
  - seasonal_order=(1,1,1,7) : composante saisonnière hebdomadaire
  - Intervalles à 80%

On garde le nom du module `prophet_forecaster` pour compat avec l'historique
mais le moteur est désormais statsmodels.

Sortie compatible avec l'ancien format Prophet pour ne pas casser le frontend.
"""

from __future__ import annotations

import logging
import time
import warnings
from collections import defaultdict
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

# Statsmodels émet beaucoup de warnings de convergence sur petits volumes :
# on les coupe ici plutôt que polluer les logs Coolify.
warnings.filterwarnings("ignore", category=UserWarning, module="statsmodels")
warnings.filterwarnings("ignore", message=".*ConvergenceWarning.*")

from statsmodels.tsa.statespace.sarimax import SARIMAX  # noqa: E402

from app.routes.sites import site_alias_match  # noqa: E402

logger = logging.getLogger(__name__)


# Cache mémoire : {site_id: (fitted_at_unix, last_history_date, fitted_model)}
_MODEL_CACHE: dict[str, tuple[float, date_cls, Any]] = {}
_CACHE_TTL_SECONDS = 3600  # 1h


def _aggregate_site_history(
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    site: dict,
) -> pd.DataFrame:
    """
    Compte journalier des messages liés à ce site (via entities.sites).
    Retourne DataFrame avec colonnes ds (date), y (count). Série continue
    (jours sans message remplis à 0).
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

    min_d = min(daily_count.keys())
    max_d = max(daily_count.keys())
    rows = []
    current = min_d
    while current <= max_d:
        rows.append({"ds": pd.Timestamp(current), "y": daily_count.get(current, 0)})
        current += timedelta(days=1)
    return pd.DataFrame(rows)


def train_forecaster(history_df: pd.DataFrame) -> Any:
    """
    Entraîne SARIMAX sur la série journalière. Modèle SARIMAX(1,1,1)x(1,1,1,7) :
    AR(1) + I(1) + MA(1) avec saisonnalité hebdomadaire SAR(1)+SI(1)+SMA(1).
    """
    series = pd.Series(
        history_df["y"].values,
        index=pd.DatetimeIndex(history_df["ds"], freq="D"),
        dtype=float,
    )
    model = SARIMAX(
        series,
        order=(1, 1, 1),
        seasonal_order=(1, 1, 1, 7),
        enforce_stationarity=False,
        enforce_invertibility=False,
    )
    fitted = model.fit(disp=False, maxiter=100)
    return fitted


def predict_horizon(
    fitted: Any,
    last_history_date: date_cls,
    horizon_days: int = 30,
) -> list[dict[str, Any]]:
    """
    Prédit `horizon_days` à partir de last_history_date.
    Retourne liste de dicts {date, yhat, yhat_lower, yhat_upper}.
    """
    forecast = fitted.get_forecast(steps=horizon_days)
    mean = forecast.predicted_mean
    ci = forecast.conf_int(alpha=0.2)  # intervalle à 80%
    lower_col, upper_col = ci.columns[0], ci.columns[1]

    out: list[dict[str, Any]] = []
    for i in range(horizon_days):
        d = last_history_date + timedelta(days=i + 1)
        yhat = max(0.0, float(mean.iloc[i]))
        lo = max(0.0, float(ci.iloc[i][lower_col]))
        up = max(0.0, float(ci.iloc[i][upper_col]))
        out.append({
            "date": d.isoformat(),
            "yhat": round(yhat, 1),
            "yhat_lower": round(lo, 1),
            "yhat_upper": round(up, 1),
        })
    return out


def forecast_site(
    site: dict,
    messages: list[dict],
    classifications_by_id: dict[str, dict],
    horizon_days: int = 30,
    *,
    history_tail_days: int | None = 90,
) -> dict[str, Any] | None:
    """
    Entraîne SARIMAX si nécessaire, prédit horizon, renvoie format compatible
    avec l'ancien Prophet (frontend inchangé).
    """
    site_id = site["id"]
    history_df = _aggregate_site_history(messages, classifications_by_id, site)

    if len(history_df) < 30:
        return None

    last_date = history_df["ds"].max().date()

    cached = _MODEL_CACHE.get(site_id)
    now = time.time()
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS and cached[1] == last_date:
        fitted = cached[2]
        logger.info("SARIMAX cache hit pour site %s", site_id)
    else:
        t0 = time.time()
        fitted = train_forecaster(history_df)
        elapsed = time.time() - t0
        logger.info(
            "SARIMAX entraîné pour site %s (%d points, %.1fs)",
            site_id, len(history_df), elapsed,
        )
        _MODEL_CACHE[site_id] = (now, last_date, fitted)

    predictions = predict_horizon(fitted, last_date, horizon_days=horizon_days)

    if history_tail_days is not None:
        cutoff = last_date - timedelta(days=history_tail_days)
        history_df = history_df[history_df["ds"].dt.date >= cutoff]

    history_out = [
        {"date": row["ds"].date().isoformat(), "actual": int(row["y"])}
        for _, row in history_df.iterrows()
    ]

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
    if len(predictions) < 7:
        return "stable"
    first_week = sum(p["yhat"] for p in predictions[:7])
    last_week = sum(p["yhat"] for p in predictions[-7:])
    if last_week > first_week * 1.15:
        return "haussiere"
    if last_week < first_week * 0.85:
        return "baissiere"
    return "stable"
