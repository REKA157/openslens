"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import {
  AiAlert,
  AiInsights,
  AlertCategory,
  AlertSeverity,
  CrossSignal,
  PredictionsData,
  RecurringFailure,
  SiteAnomaly,
  SiteForecast,
  SiteTrend,
  fetchPredictions,
  generatePredictionsInsights,
} from "@/lib/api";

const SEVERITY_STYLES: Record<
  AlertSeverity,
  { ring: string; chip: string; label: string }
> = {
  critical: {
    ring: "border-red-400 bg-red-50 dark:border-red-900 dark:bg-red-950/30",
    chip: "bg-red-600 text-white",
    label: "CRITIQUE",
  },
  warning: {
    ring: "border-amber-400 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30",
    chip: "bg-amber-500 text-white",
    label: "ATTENTION",
  },
  info: {
    ring: "border-blue-300 bg-blue-50 dark:border-blue-900 dark:bg-blue-950/20",
    chip: "bg-blue-500 text-white",
    label: "INFO",
  },
};

const CATEGORY_LABEL: Record<AlertCategory, string> = {
  surcharge: "Surcharge opérationnelle",
  qualite_securite: "Qualité / sécurité",
  equipement: "Équipement",
  silence_anormal: "Silence anormal",
  opportunite: "Opportunité",
};

const TIMELINE_LABEL: Record<string, string> = {
  immediat: "à traiter aujourd'hui",
  cette_semaine: "cette semaine",
  ce_mois: "ce mois",
};

export default function PredictionsPage() {
  const router = useRouter();
  const [data, setData] = useState<PredictionsData | null>(null);
  const [insights, setInsights] = useState<AiInsights | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
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

  async function handleGenerateInsights() {
    setGenerating(true);
    setError(null);
    try {
      const enriched = await generatePredictionsInsights();
      setData(enriched);
      setInsights(enriched.insights);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
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
          </div>
          <button
            onClick={handleGenerateInsights}
            disabled={generating || loading || !data}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-900 dark:hover:bg-zinc-200"
            title="Croise signaux + messages réels via Claude Sonnet (~0,08 $)"
          >
            {generating
              ? "Analyse IA en cours (15-30 s)…"
              : insights
                ? "Régénérer les insights IA"
                : "🧠 Générer insights IA"}
          </button>
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
            Calcul des signaux (2-5 sec)…
          </p>
        )}

        {insights && <InsightsOverview insights={insights} />}

        {data && !loading && (
          <>
            <details className="rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
              <summary className="cursor-pointer px-4 py-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Détail des signaux statistiques bruts
              </summary>
              <div className="space-y-6 border-t border-zinc-200 px-4 py-4 dark:border-zinc-800">
                <AnomaliesSection items={data.anomalies} />
                <TrendsSection items={data.trends} />
                <ForecastSection items={data.forecast} />
                <FailuresSection items={data.recurring_failures} />
              </div>
            </details>
          </>
        )}
      </main>
    </div>
  );
}

// ---------- AI Insights ----------

function InsightsOverview({ insights }: { insights: AiInsights }) {
  return (
    <div className="space-y-6">
      {insights.narrative_overview && (
        <section className="rounded-lg border border-zinc-300 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-900">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Synthèse opérationnelle
          </div>
          <p className="text-sm leading-relaxed text-zinc-800 dark:text-zinc-200">
            {insights.narrative_overview}
          </p>
        </section>
      )}

      {insights.alerts.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Alertes priorisées ({insights.alerts.length})
          </h2>
          <div className="space-y-3">
            {insights.alerts.map((a, i) => (
              <AlertCard key={i} alert={a} />
            ))}
          </div>
        </section>
      )}

      {insights.cross_signals.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Signaux croisés multi-sites
          </h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {insights.cross_signals.map((c, i) => (
              <CrossSignalCard key={i} signal={c} />
            ))}
          </div>
        </section>
      )}

      {Object.keys(insights.recommendations_by_site).length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Recommandations par site
          </h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {Object.entries(insights.recommendations_by_site).map(
              ([site, recos]) => (
                <div
                  key={site}
                  className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900"
                >
                  <div className="mb-2 text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                    {site}
                  </div>
                  <ul className="space-y-1.5 text-xs">
                    {recos.map((r, i) => (
                      <li
                        key={i}
                        className="flex items-start gap-2 text-zinc-700 dark:text-zinc-300"
                      >
                        <span className="mt-1 inline-block h-1 w-1 shrink-0 rounded-full bg-zinc-500" />
                        {r}
                      </li>
                    ))}
                  </ul>
                </div>
              ),
            )}
          </div>
        </section>
      )}
    </div>
  );
}

function AlertCard({ alert }: { alert: AiAlert }) {
  const style = SEVERITY_STYLES[alert.severity] || SEVERITY_STYLES.info;
  const body = (
    <div className={`rounded-lg border-2 p-4 ${style.ring}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            className={`rounded px-2 py-0.5 text-[10px] font-semibold ${style.chip}`}
          >
            {style.label}
          </span>
          <span className="rounded bg-zinc-200 px-2 py-0.5 text-[10px] font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
            {CATEGORY_LABEL[alert.category] || alert.category}
          </span>
          <span className="text-[10px] uppercase text-zinc-500">
            {TIMELINE_LABEL[alert.timeline] || alert.timeline}
          </span>
        </div>
        <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
          {alert.site_name}
        </span>
      </div>
      <h3 className="mt-2 text-base font-semibold text-zinc-900 dark:text-zinc-50">
        {alert.title}
      </h3>
      <p className="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
        {alert.evidence}
      </p>
      {alert.recommended_actions?.length > 0 && (
        <div className="mt-3 border-t border-zinc-200 pt-3 dark:border-zinc-800">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
            Actions recommandées
          </div>
          <ul className="space-y-1 text-sm text-zinc-800 dark:text-zinc-200">
            {alert.recommended_actions.map((act, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-zinc-700 dark:bg-zinc-300" />
                {act}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
  return alert.site_id ? (
    <Link href={`/sites/${alert.site_id}`} className="block hover:opacity-95">
      {body}
    </Link>
  ) : (
    body
  );
}

function CrossSignalCard({ signal }: { signal: CrossSignal }) {
  return (
    <div className="rounded-lg border border-purple-200 bg-purple-50 p-3 dark:border-purple-900 dark:bg-purple-950/20">
      <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
        {signal.title}
      </h3>
      {signal.involved_sites?.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {signal.involved_sites.map((s) => (
            <span
              key={s}
              className="rounded bg-purple-100 px-1.5 py-0.5 text-[10px] text-purple-800 dark:bg-purple-950 dark:text-purple-300"
            >
              {s}
            </span>
          ))}
        </div>
      )}
      <p className="mt-2 text-xs text-zinc-700 dark:text-zinc-300">
        {signal.explanation}
      </p>
      {signal.implications && (
        <p className="mt-1 text-xs italic text-zinc-600 dark:text-zinc-400">
          → {signal.implications}
        </p>
      )}
    </div>
  );
}

// ---------- Sections statistiques (déplacées dans le repli) ----------

function AnomaliesSection({ items }: { items: SiteAnomaly[] }) {
  return (
    <Section
      title="Anomalies cette semaine"
      subtitle="Z-score volume + urgences vs 12 dernières semaines (|Z| ≥ 2)."
      empty={
        items.length === 0
          ? "Aucune anomalie statistique détectée."
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
      subtitle="Évolutions par site et catégorie (|delta| ≥ 25%)."
      empty={
        items.length === 0
          ? "Aucune tendance significative."
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
                  <span className="ml-2 text-xs text-zinc-500">{t.region}</span>
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
      subtitle="Volume attendu par site et par jour."
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
      subtitle="Engins / véhicules mentionnés ≥ 3 fois dans des incidents."
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
                    <span className="text-zinc-400">{ex.date}</span>{" "}
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
        <h3 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {title}
        </h3>
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
