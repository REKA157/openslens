"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import {
  fetchLatestDailyReport,
  generateDailyReport,
  DailyReport,
} from "@/lib/api";

export default function DailyReportPage() {
  const router = useRouter();
  const [report, setReport] = useState<DailyReport | null>(null);
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
      const r = await fetchLatestDailyReport();
      setReport(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleGenerate(force = false) {
    setGenerating(true);
    setError(null);
    try {
      const r = await generateDailyReport(force);
      setReport(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-4xl space-y-6 px-4 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
              Rapport quotidien
            </h1>
            {report && (
              <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
                {new Date(report.report_date).toLocaleDateString("fr-FR", {
                  weekday: "long",
                  year: "numeric",
                  month: "long",
                  day: "numeric",
                })}{" "}
                · {report.stats.total_messages} message
                {report.stats.total_messages > 1 ? "s" : ""} · généré par{" "}
                {report.model_used}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => handleGenerate(false)}
              disabled={generating}
              className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
            >
              {generating ? "Génération…" : "Générer aujourd'hui"}
            </button>
            {report && (
              <button
                onClick={() => handleGenerate(true)}
                disabled={generating}
                className="rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-900 dark:hover:bg-zinc-200"
              >
                {generating ? "…" : "Régénérer"}
              </button>
            )}
          </div>
        </div>

        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">
            {error}
          </div>
        )}

        {loading && <p className="text-sm text-zinc-500">Chargement…</p>}

        {!loading && !report && !error && (
          <div className="rounded-lg border border-dashed border-zinc-300 p-8 text-center dark:border-zinc-700">
            <p className="text-sm text-zinc-500">
              Aucun rapport encore généré.
            </p>
            <button
              onClick={() => handleGenerate(false)}
              disabled={generating}
              className="mt-3 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-900 dark:hover:bg-zinc-200"
            >
              {generating ? "Génération en cours…" : "Générer maintenant"}
            </button>
          </div>
        )}

        {report && !loading && (
          <div className="space-y-6">
            {/* Headline + narrative */}
            <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
              <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">
                {report.content.headline}
              </h2>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
                {report.content.narrative}
              </p>
            </section>

            {/* Points d'attention */}
            {report.content.urgent_points?.length > 0 && (
              <section className="rounded-lg border border-red-200 bg-red-50 p-6 dark:border-red-900 dark:bg-red-950/20">
                <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-red-700 dark:text-red-400">
                  Points d&apos;attention
                </h3>
                <ul className="space-y-2">
                  {report.content.urgent_points.map((p, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-2 text-sm text-zinc-800 dark:text-zinc-200"
                    >
                      <span className="mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-red-500" />
                      {p}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Site activity */}
            {Object.keys(report.content.site_activity || {}).length > 0 && (
              <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
                <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Activité par site
                </h3>
                <div className="space-y-3">
                  {Object.entries(report.content.site_activity).map(
                    ([site, summary]) => (
                      <div key={site}>
                        <h4 className="text-sm font-medium text-zinc-900 dark:text-zinc-50">
                          {site}
                        </h4>
                        <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                          {summary}
                        </p>
                      </div>
                    )
                  )}
                </div>
              </section>
            )}

            {/* Open actions */}
            {report.content.open_actions?.length > 0 && (
              <section className="rounded-lg border border-purple-200 bg-purple-50 p-6 dark:border-purple-900 dark:bg-purple-950/20">
                <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-purple-700 dark:text-purple-400">
                  Actions ouvertes en fin de journée
                </h3>
                <ul className="space-y-2">
                  {report.content.open_actions.map((a, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-2 text-sm text-zinc-800 dark:text-zinc-200"
                    >
                      <span className="mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-purple-500" />
                      {a}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Recommendations */}
            {report.content.recommendations?.length > 0 && (
              <section className="rounded-lg border border-emerald-200 bg-emerald-50 p-6 dark:border-emerald-900 dark:bg-emerald-950/20">
                <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-400">
                  Recommandations
                </h3>
                <ul className="space-y-2">
                  {report.content.recommendations.map((r, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-2 text-sm text-zinc-800 dark:text-zinc-200"
                    >
                      <span className="mt-1 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
                      {r}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
