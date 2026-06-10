"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import {
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

export default function ForecastPage() {
  const router = useRouter();
  const [data, setData] = useState<ForecastResponse | null>(null);
  const [horizon, setHorizon] = useState<7 | 14 | 30>(14);
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

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
              Prévisions de volume
            </h1>
            <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              Modèle Prophet (Meta) entraîné par site sur 10 mois d&apos;historique.
              Intègre saisonnalité hebdo/mensuelle et jours fériés français.
              Intervalles de confiance à 80%.
            </p>
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
            Entraînement des modèles Prophet (30-90 sec au premier appel)…
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

  // Combine historique récent (30 dernières observations) + prédictions
  // pour visualisation en histogramme
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
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <Link
            href={`/sites/${site.site_id}`}
            className="text-base font-semibold text-zinc-900 hover:underline dark:text-zinc-50"
          >
            {site.site_name}
          </Link>
          {site.region && (
            <span className="ml-2 text-xs text-zinc-500">{site.region}</span>
          )}
        </div>
        <div className={`text-xs font-medium ${trend.cls}`}>{trend.label}</div>
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

      {/* Mini graphique : barres historique récent + prédictions */}
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
        <Legend cls="bg-blue-500" label="Prédiction Prophet" />
        <Legend cls="bg-blue-200 dark:bg-blue-900" label="Intervalle 80%" />
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
