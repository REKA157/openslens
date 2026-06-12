"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import {
  ForecastPredictionPoint,
  ForecastResponse,
  SiteForecastModel,
  fetchForecast,
} from "@/lib/api";

const HORIZONS = [7, 14, 30] as const;

const TREND_LABELS = {
  haussiere: { label: "↗ Haussière", cls: "text-emerald-700 dark:text-emerald-400" },
  baissiere: { label: "↘ Baissière", cls: "text-red-700 dark:text-red-400" },
  stable: { label: "→ Stable", cls: "text-zinc-600 dark:text-zinc-400" },
} as const;

const DAY_NAMES_FR = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"];

const EXUTOIRE_STATUS: Record<string, { label: string; cls: string }> = {
  ok: { label: "Conforme", cls: "bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-300" },
  sur_objectif: { label: "Au-dessus max", cls: "bg-blue-100 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300" },
  sous_objectif: { label: "Sous objectif", cls: "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300" },
  critique: { label: "Critique", cls: "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300" },
};

// ---------- Logique business (heuristiques) ----------

function recentObservedTotal(site: SiteForecastModel): number {
  const last7 = site.history.slice(-7);
  return last7.reduce((sum, p) => sum + p.actual, 0);
}

function peakPrediction(predictions: ForecastPredictionPoint[]):
  | { date: string; value: number; dayName: string }
  | null {
  if (predictions.length === 0) return null;
  let peak = predictions[0];
  for (const p of predictions) {
    if (p.yhat > peak.yhat) peak = p;
  }
  const d = new Date(peak.date + "T00:00:00Z");
  return {
    date: peak.date,
    value: peak.yhat,
    dayName: DAY_NAMES_FR[d.getUTCDay()],
  };
}

type RecoLevel = "alert" | "warning" | "info" | "success";

function siteRecommendation(site: SiteForecastModel): {
  level: RecoLevel;
  title: string;
  message: string;
} {
  const recent = recentObservedTotal(site);
  const expected = site.summary.expected_total;
  const lower = site.summary.expected_lower;
  const upper = site.summary.expected_upper;
  const uncertainty = expected > 0.5 ? (upper - lower) / expected : 0;

  // Chute prononcée
  if (recent >= 3 && expected / Math.max(recent, 1) < 0.4) {
    return {
      level: "alert",
      title: "Possible chute d'activité",
      message: `Vous avez observé ${recent} messages la semaine passée mais seulement ${expected.toFixed(1)} sont attendus. À investiguer (perte client, site inactif, congé responsable).`,
    };
  }

  // Forte tension
  if (expected >= 20) {
    return {
      level: "warning",
      title: "Site en forte tension",
      message: "Affecter 2-3 chauffeurs dédiés. Anticiper le stock de bennes pour le pic de la semaine.",
    };
  }

  if (expected >= 10) {
    return {
      level: "info",
      title: "Charge modérée",
      message: "1-2 chauffeurs suffisent. Surveiller le jour de pic prévu.",
    };
  }

  // Volatilité élevée (peu fiable)
  if (uncertainty > 1.5 && expected > 1) {
    return {
      level: "info",
      title: "Prédiction peu fiable",
      message: "Peu de données historiques ou pattern erratique. À surveiller manuellement.",
    };
  }

  return {
    level: "success",
    title: "Activité résiduelle",
    message: "Volume faible attendu, pas de tension opérationnelle prévue.",
  };
}

const RECO_STYLES: Record<RecoLevel, { ring: string; chip: string; chipLabel: string }> = {
  alert: {
    ring: "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/30",
    chip: "bg-red-600 text-white",
    chipLabel: "ALERTE",
  },
  warning: {
    ring: "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30",
    chip: "bg-amber-500 text-white",
    chipLabel: "ATTENTION",
  },
  info: {
    ring: "border-blue-200 bg-blue-50 dark:border-blue-900 dark:bg-blue-950/20",
    chip: "bg-blue-500 text-white",
    chipLabel: "INFO",
  },
  success: {
    ring: "border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900",
    chip: "bg-zinc-400 text-white",
    chipLabel: "OK",
  },
};

// ---------- Page ----------

export default function ForecastPage() {
  const router = useRouter();
  const [data, setData] = useState<ForecastResponse | null>(null);
  const [horizon, setHorizon] = useState<7 | 14 | 30>(7);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
    });
  }, [router]);

  const load = useCallback(
    async (h: number) => {
      setLoading(true);
      setError(null);
      try {
        const r = await fetchForecast(h);
        setData(r);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    load(horizon);
  }, [horizon, load]);

  const overall = useMemo(() => {
    if (!data || data.sites.length === 0) return null;
    const total = data.sites.reduce((s, x) => s + x.summary.expected_total, 0);
    const totalLower = data.sites.reduce((s, x) => s + x.summary.expected_lower, 0);
    const totalUpper = data.sites.reduce((s, x) => s + x.summary.expected_upper, 0);
    const byRegion = new Map<string, number>();
    for (const s of data.sites) {
      const r = s.region || "Autres";
      byRegion.set(r, (byRegion.get(r) ?? 0) + s.summary.expected_total);
    }
    const regions = Array.from(byRegion.entries())
      .map(([region, expected]) => ({
        region,
        expected: Math.round(expected * 10) / 10,
        pct: total > 0 ? (expected / total) * 100 : 0,
      }))
      .sort((a, b) => b.expected - a.expected);

    // Top alertes : sites avec recommandation alert ou warning
    const alerts = data.sites
      .map((s) => ({ site: s, reco: siteRecommendation(s) }))
      .filter((r) => r.reco.level === "alert" || r.reco.level === "warning");

    return {
      total: Math.round(total * 10) / 10,
      totalLower: Math.round(totalLower * 10) / 10,
      totalUpper: Math.round(totalUpper * 10) / 10,
      regions,
      alerts,
    };
  }, [data]);

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
              Prévisions de volume
            </h1>
            {data && (
              <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                Réf. {data.ref_date} · {data.modelled_count}/{data.sites_count} sites
                modélisés · {data.messages_scanned} messages
              </p>
            )}
          </div>
          <div className="inline-flex overflow-hidden rounded-md border border-zinc-200 dark:border-zinc-700">
            {HORIZONS.map((h) => (
              <button
                key={h}
                onClick={() => setHorizon(h)}
                className={`px-3 py-1.5 text-xs font-medium transition ${
                  horizon === h
                    ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "bg-white text-zinc-700 hover:bg-zinc-50 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
                }`}
              >
                J+{h}
              </button>
            ))}
          </div>
        </header>

        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">
            {error}
          </div>
        )}

        {loading && (
          <p className="text-sm text-zinc-500">
            Calcul des prévisions par site (5-15 sec)…
          </p>
        )}

        {data?.warning && (
          <div className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
            {data.warning}
          </div>
        )}

        {data && !loading && data.sites.length === 0 && (
          <div className="rounded-md border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700">
            Aucun site n&apos;a assez d&apos;historique pour être modélisé (minimum
            30 jours requis).
          </div>
        )}

        {/* Récap global */}
        {overall && (
          <section className="rounded-lg border-2 border-zinc-300 bg-white p-5 dark:border-zinc-700 dark:bg-zinc-900">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <div>
                <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Synthèse — {horizon} prochains jours
                </h2>
                <div className="mt-1 text-3xl font-bold text-zinc-900 dark:text-zinc-50">
                  {overall.total}{" "}
                  <span className="text-base font-normal text-zinc-500">
                    messages attendus sur ADS
                  </span>
                </div>
                <div className="mt-1 text-sm text-zinc-500">
                  Plancher {overall.totalLower} · Plafond {overall.totalUpper} (confiance 80%)
                </div>
              </div>
            </div>

            <div className="mt-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Ventilation par région
              </div>
              <div className="mt-2 space-y-2">
                {overall.regions.map((r) => (
                  <div key={r.region}>
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-medium text-zinc-700 dark:text-zinc-300">
                        {r.region}
                      </span>
                      <span className="text-zinc-500">
                        {r.expected} msg ·{" "}
                        <span className="font-medium">
                          {Math.round(r.pct)}%
                        </span>
                      </span>
                    </div>
                    <div className="mt-1 h-2 overflow-hidden rounded bg-zinc-100 dark:bg-zinc-800">
                      <div
                        className="h-full rounded bg-zinc-700 dark:bg-zinc-300"
                        style={{ width: `${r.pct}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {overall.alerts.length > 0 && (
              <div className="mt-4 border-t border-zinc-200 pt-4 dark:border-zinc-800">
                <div className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Sites à surveiller cette semaine ({overall.alerts.length})
                </div>
                <ul className="mt-2 space-y-1.5 text-sm">
                  {overall.alerts.map(({ site, reco }) => (
                    <li
                      key={site.site_id}
                      className="flex items-start gap-2"
                    >
                      <span
                        className={`mt-0.5 rounded px-1.5 py-0.5 text-[10px] font-semibold ${RECO_STYLES[reco.level].chip}`}
                      >
                        {RECO_STYLES[reco.level].chipLabel}
                      </span>
                      <span>
                        <Link
                          href={`/sites/${site.site_id}`}
                          className="font-medium text-zinc-900 hover:underline dark:text-zinc-50"
                        >
                          {site.site_name}
                        </Link>{" "}
                        <span className="text-zinc-500">— {reco.title}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}

        {/* Projection contractuelle des exutoires (déchets ultimes) */}
        {data?.exutoires_projection && data.exutoires_projection.exutoires.length > 0 && (
          <section className="rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Atteinte contractuelle exutoires — projection fin {data.exutoires_projection.year}
            </h2>
            <div className="mt-1 text-sm text-zinc-500">
              Global : projection{" "}
              <span className="font-semibold text-zinc-900 dark:text-zinc-50">
                {Math.round(data.exutoires_projection.totals.projection_annual).toLocaleString("fr-FR")} t
              </span>{" "}
              / {Math.round(data.exutoires_projection.totals.contractual_annual).toLocaleString("fr-FR")} t engagés
              {" "}
              <span className={data.exutoires_projection.totals.delta_projection < 0 ? "text-red-600" : "text-emerald-600"}>
                ({data.exutoires_projection.totals.delta_projection > 0 ? "+" : ""}
                {Math.round(data.exutoires_projection.totals.delta_projection).toLocaleString("fr-FR")} t)
              </span>
            </div>
            <div className="mt-3 space-y-1.5">
              {data.exutoires_projection.exutoires.map((e) => {
                const st = EXUTOIRE_STATUS[e.status] || EXUTOIRE_STATUS.ok;
                return (
                  <div key={e.name} className="flex flex-wrap items-center justify-between gap-2 border-t border-zinc-100 py-1.5 text-sm dark:border-zinc-800">
                    <span className="font-medium text-zinc-800 dark:text-zinc-200">{e.name}</span>
                    <span className="flex items-center gap-3">
                      <span className="text-zinc-500">
                        ≈ {Math.round(e.projection_annual).toLocaleString("fr-FR")} / {Math.round(e.contractual_annual_min).toLocaleString("fr-FR")} t
                        {e.pct_projection != null && ` (${e.pct_projection}%)`}
                      </span>
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${st.cls}`}>{st.label}</span>
                    </span>
                  </div>
                );
              })}
            </div>
            <p className="mt-3 text-[11px] text-zinc-400">
              Projection linéaire à rythme constant. Détail mensuel dans l&apos;onglet Exutoires.
            </p>
          </section>
        )}

        {/* Cards par site */}
        {data && !loading && data.sites.length > 0 && (
          <div className="space-y-4">
            {data.sites.map((s) => (
              <SiteForecastCard key={s.site_id} site={s} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

function SiteForecastCard({ site }: { site: SiteForecastModel }) {
  const trend = TREND_LABELS[site.summary.trend];
  const reco = siteRecommendation(site);
  const peak = peakPrediction(site.predictions);
  const recoStyle = RECO_STYLES[reco.level];

  const recentHistory = useMemo(
    () => site.history.slice(-30),
    [site.history],
  );

  const allDatasetMax = useMemo(() => {
    const histMax = Math.max(...recentHistory.map((p) => p.actual), 1);
    const predMax = Math.max(...site.predictions.map((p) => p.yhat_upper), 1);
    return Math.max(histMax, predMax);
  }, [recentHistory, site.predictions]);

  return (
    <section className={`rounded-lg border p-4 ${recoStyle.ring}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div className="flex items-center gap-2">
          <Link
            href={`/sites/${site.site_id}`}
            className="text-base font-semibold text-zinc-900 hover:underline dark:text-zinc-50"
          >
            {site.site_name}
          </Link>
          {site.region && (
            <span className="text-xs text-zinc-500">{site.region}</span>
          )}
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${recoStyle.chip}`}
          >
            {recoStyle.chipLabel}
          </span>
        </div>
        <div className={`text-xs font-medium ${trend.cls}`}>{trend.label}</div>
      </div>

      {/* Recommandation business */}
      <div className="mt-2 rounded bg-white/60 px-3 py-2 text-sm dark:bg-zinc-950/40">
        <div className="font-medium text-zinc-900 dark:text-zinc-100">
          {reco.title}
        </div>
        <div className="mt-0.5 text-zinc-700 dark:text-zinc-300">
          {reco.message}
        </div>
        {peak && peak.value > 0 && (
          <div className="mt-1 text-xs text-zinc-500">
            Jour de pic prévu : <span className="font-medium">{peak.dayName}</span>{" "}
            ({peak.date}) avec {peak.value.toFixed(1)} messages attendus
          </div>
        )}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        <Stat
          label={`Total attendu J+${site.horizon_days}`}
          value={site.summary.expected_total}
        />
        <Stat
          label="Plancher (80%)"
          value={site.summary.expected_lower}
          muted
        />
        <Stat
          label="Plafond (80%)"
          value={site.summary.expected_upper}
          muted
        />
        <Stat
          label="Historique"
          value={`${site.summary.history_days} j`}
          muted
        />
      </div>

      <div className="mt-4 overflow-x-auto">
        <div className="flex items-end gap-0.5" style={{ minWidth: "600px" }}>
          {recentHistory.map((p, i) => (
            <Bar
              key={`h${i}`}
              date={p.date}
              value={p.actual}
              max={allDatasetMax}
              kind="history"
            />
          ))}
          <div className="mx-1 h-16 w-px self-end bg-zinc-300 dark:bg-zinc-600" />
          {site.predictions.map((p, i) => (
            <Bar
              key={`p${i}`}
              date={p.date}
              value={p.yhat}
              upper={p.yhat_upper}
              lower={p.yhat_lower}
              max={allDatasetMax}
              kind="prediction"
            />
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[10px] text-zinc-500">
          <span>← {recentHistory[0]?.date || ""}</span>
          <span>Aujourd&apos;hui ↕</span>
          <span>
            {site.predictions[site.predictions.length - 1]?.date || ""} →
          </span>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-zinc-500">
        <Legend cls="bg-zinc-500" label="Historique observé" />
        <Legend cls="bg-blue-500" label="Prévision" />
        <Legend cls="bg-blue-200 dark:bg-blue-900" label="Intervalle de confiance 80%" />
      </div>
    </section>
  );
}

function Bar({
  date,
  value,
  upper,
  lower,
  max,
  kind,
}: {
  date: string;
  value: number;
  upper?: number;
  lower?: number;
  max: number;
  kind: "history" | "prediction";
}) {
  const hPct = (value / max) * 100;
  const uPct = upper != null ? (upper / max) * 100 : 0;
  const lPct = lower != null ? (lower / max) * 100 : 0;

  return (
    <div
      className="relative flex h-16 w-3 flex-col items-stretch justify-end"
      title={
        kind === "history"
          ? `${date} : ${value} message(s)`
          : `${date} : ${value} (${lower}-${upper}) attendus`
      }
    >
      {kind === "prediction" && upper != null && lower != null && (
        <div
          className="absolute left-1/2 w-2 -translate-x-1/2 rounded-sm bg-blue-200 dark:bg-blue-900/60"
          style={{
            bottom: `${lPct}%`,
            height: `${uPct - lPct}%`,
          }}
        />
      )}
      <div
        className={`relative w-full rounded-t-sm ${
          kind === "history"
            ? "bg-zinc-500 dark:bg-zinc-400"
            : "bg-blue-500 dark:bg-blue-400"
        }`}
        style={{ height: `${hPct}%` }}
      />
    </div>
  );
}

function Stat({
  label,
  value,
  muted,
}: {
  label: string;
  value: number | string;
  muted?: boolean;
}) {
  return (
    <div>
      <div className="text-zinc-500 dark:text-zinc-400">{label}</div>
      <div
        className={`mt-0.5 text-lg font-semibold ${muted ? "text-zinc-500 dark:text-zinc-400" : "text-zinc-900 dark:text-zinc-50"}`}
      >
        {value}
      </div>
    </div>
  );
}

function Legend({ cls, label }: { cls: string; label: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className={`inline-block h-2 w-3 rounded ${cls}`} />
      {label}
    </span>
  );
}
