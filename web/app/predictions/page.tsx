"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import {
  PredictionsData,
  RecurringFailure,
  SiteAnomaly,
  SiteForecast,
  SiteTrend,
  fetchPredictions,
} from "@/lib/api";

export default function PredictionsPage() {
  const router = useRouter();
  const [data, setData] = useState<PredictionsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
    });
  }, [router]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await fetchPredictions();
      setData(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        <header>
          <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
            Intelligence prédictive
          </h1>
          {data && (
            <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              Référence : {data.ref_date} · {data.sites_count} sites ·{" "}
              {data.messages_scanned} messages ·{" "}
              {data.classifications_loaded} classifs IA
            </p>
          )}
        </header>

        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">
            {error}
          </div>
        )}
        {data?.warning && (
          <div className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
            {data.warning}
          </div>
        )}

        {loading && (
          <p className="text-sm text-zinc-500">
            Calcul en cours (peut prendre 2-5 secondes selon le volume)…
          </p>
        )}

        {data && !loading && (
          <>
            <AnomaliesSection items={data.anomalies} />
            <TrendsSection items={data.trends} />
            <ForecastSection items={data.forecast} />
            <FailuresSection items={data.recurring_failures} />
          </>
        )}
      </main>
    </div>
  );
}

// ---------- Sections ----------

function AnomaliesSection({ items }: { items: SiteAnomaly[] }) {
  return (
    <Section
      title="Anomalies cette semaine"
      subtitle="Sites dont le volume ou les urgences s'écartent fortement de la moyenne des 12 dernières semaines (Z-score ≥ 2)."
      empty={
        items.length === 0
          ? "Aucune anomalie détectée — tout est dans la normale."
          : undefined
      }
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {items.map((a) => {
          const zMax = Math.max(
            Math.abs(a.volume.z_score ?? 0),
            Math.abs(a.urgent.z_score ?? 0),
          );
          const severityColor =
            a.severity === "high"
              ? "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/20"
              : "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/20";
          return (
            <Link
              key={a.site_id}
              href={`/sites/${a.site_id}`}
              className={`rounded-lg border p-3 transition hover:shadow-md ${severityColor}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                    {a.site_name}
                  </div>
                  {a.region && (
                    <div className="text-[11px] text-zinc-500">{a.region}</div>
                  )}
                </div>
                <div className="text-right">
                  <div
                    className={`text-xs font-semibold ${
                      a.severity === "high"
                        ? "text-red-700 dark:text-red-400"
                        : "text-amber-700 dark:text-amber-400"
                    }`}
                  >
                    Z = {zMax.toFixed(1)}
                  </div>
                  <div className="text-[10px] uppercase text-zinc-500">
                    {a.severity === "high" ? "Sévère" : "Modéré"}
                  </div>
                </div>
              </div>
              <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <div className="text-zinc-500">Volume</div>
                  <div className="font-medium text-zinc-900 dark:text-zinc-100">
                    {a.volume.current}{" "}
                    <span className="text-zinc-400">
                      (moy. {a.volume.mean_history})
                    </span>
                  </div>
                </div>
                <div>
                  <div className="text-zinc-500">Urgents</div>
                  <div className="font-medium text-zinc-900 dark:text-zinc-100">
                    {a.urgent.current}{" "}
                    <span className="text-zinc-400">
                      (moy. {a.urgent.mean_history})
                    </span>
                  </div>
                </div>
              </div>
            </Link>
          );
        })}
      </div>
    </Section>
  );
}

function TrendsSection({ items }: { items: SiteTrend[] }) {
  return (
    <Section
      title="Tendances longues (4 vs 4 semaines)"
      subtitle="Évolutions par site et par catégorie sur les 4 dernières semaines comparées aux 4 précédentes (>=25%)."
      empty={
        items.length === 0
          ? "Aucune tendance significative ce mois."
          : undefined
      }
    >
      <div className="space-y-3">
        {items.map((t) => (
          <Link
            key={t.site_id}
            href={`/sites/${t.site_id}`}
            className="block rounded-lg border border-zinc-200 bg-white p-3 hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div>
                <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                  {t.site_name}
                </span>
                {t.region && (
                  <span className="ml-2 text-xs text-zinc-500">
                    {t.region}
                  </span>
                )}
              </div>
              <DeltaPct value={t.volume.delta_pct} label="volume" />
            </div>
            {Object.keys(t.by_category).length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.entries(t.by_category).map(([cat, d]) => (
                  <span
                    key={cat}
                    className="rounded bg-zinc-100 px-2 py-0.5 text-[11px] dark:bg-zinc-800"
                  >
                    {cat} :{" "}
                    <span
                      className={
                        d.delta_pct > 0
                          ? "text-emerald-700 dark:text-emerald-400"
                          : d.delta_pct < 0
                            ? "text-red-700 dark:text-red-400"
                            : "text-zinc-500"
                      }
                    >
                      {d.delta_pct > 0 ? "+" : ""}
                      {d.delta_pct}%
                    </span>{" "}
                    <span className="text-zinc-400">
                      ({d.recent}/{d.prev})
                    </span>
                  </span>
                ))}
              </div>
            )}
          </Link>
        ))}
      </div>
    </Section>
  );
}

function ForecastSection({ items }: { items: SiteForecast[] }) {
  return (
    <Section
      title="Prévision semaine prochaine"
      subtitle="Volume attendu par site et par jour, basé sur la saisonnalité hebdomadaire des 12 dernières semaines."
      empty={
        items.length === 0
          ? "Pas assez d'historique pour prévoir."
          : undefined
      }
    >
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {items.map((f) => (
          <div
            key={f.site_id}
            className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <Link
                href={`/sites/${f.site_id}`}
                className="text-sm font-semibold text-zinc-900 hover:underline dark:text-zinc-50"
              >
                {f.site_name}
              </Link>
              <div className="text-right">
                <div className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
                  {f.expected_total}
                </div>
                <div className="text-[10px] text-zinc-500">
                  ±{f.confidence_band} sur la semaine
                </div>
              </div>
            </div>
            <div className="mt-2 grid grid-cols-7 gap-1 text-center text-[10px]">
              {f.by_day.map((d) => {
                const max = Math.max(...f.by_day.map((x) => x.expected), 1);
                const heightPct = (d.expected / max) * 100;
                return (
                  <div key={d.day} className="flex flex-col items-center gap-1">
                    <div className="flex h-12 w-full items-end">
                      <div
                        className="w-full rounded-sm bg-zinc-300 dark:bg-zinc-600"
                        style={{ height: `${heightPct}%` }}
                        title={`${d.expected} ± ${d.stdev}`}
                      />
                    </div>
                    <div className="text-zinc-500">{d.day}</div>
                    <div className="font-medium text-zinc-700 dark:text-zinc-300">
                      {d.expected}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function FailuresSection({ items }: { items: RecurringFailure[] }) {
  return (
    <Section
      title="Pannes récurrentes (3 derniers mois)"
      subtitle="Engins / véhicules mentionnés au moins 3 fois dans des incidents ou pannes."
      empty={
        items.length === 0
          ? "Aucun engin avec récurrence d'incidents repérée."
          : undefined
      }
    >
      <div className="space-y-3">
        {items.map((f) => (
          <details
            key={`${f.site_id}-${f.vehicle}`}
            className="rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900"
          >
            <summary className="cursor-pointer list-none px-3 py-2 text-sm">
              <span className="font-semibold text-zinc-900 dark:text-zinc-50">
                {f.vehicle}
              </span>
              <span className="ml-2 text-zinc-500">
                · {f.site_name} ·{" "}
                <span className="font-medium text-red-700 dark:text-red-400">
                  {f.incidents_count} incidents
                </span>
              </span>
            </summary>
            <div className="border-t border-zinc-100 px-3 py-2 dark:border-zinc-800">
              <ul className="space-y-1 text-xs">
                {f.examples.map((ex, i) => (
                  <li key={i} className="text-zinc-700 dark:text-zinc-300">
                    <span className="text-zinc-400">{ex.date}</span> ·{" "}
                    {ex.priority && (
                      <span className="rounded bg-zinc-100 px-1 py-0.5 text-[10px] dark:bg-zinc-800">
                        {ex.priority}
                      </span>
                    )}{" "}
                    {ex.summary}
                  </li>
                ))}
              </ul>
            </div>
          </details>
        ))}
      </div>
    </Section>
  );
}

// ---------- UI helpers ----------

function Section({
  title,
  subtitle,
  empty,
  children,
}: {
  title: string;
  subtitle?: string;
  empty?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {title}
        </h2>
        {subtitle && (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">{subtitle}</p>
        )}
      </div>
      {empty ? (
        <p className="rounded-md border border-dashed border-zinc-300 p-4 text-center text-xs text-zinc-500 dark:border-zinc-700">
          {empty}
        </p>
      ) : (
        children
      )}
    </section>
  );
}

function DeltaPct({ value, label }: { value: number; label: string }) {
  const cls =
    value > 0
      ? "text-emerald-700 dark:text-emerald-400"
      : value < 0
        ? "text-red-700 dark:text-red-400"
        : "text-zinc-500";
  return (
    <div className={`text-sm font-semibold ${cls}`}>
      {value > 0 ? "+" : ""}
      {value}% <span className="text-[10px] uppercase opacity-70">{label}</span>
    </div>
  );
}
