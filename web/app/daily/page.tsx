"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import {
  fetchDailyReportByDate,
  fetchDailyReportsList,
  generateDailyReport,
  DailyReport,
  DailyReportSummary,
} from "@/lib/api";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function shiftDay(iso: string, dir: -1 | 1): string {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + dir);
  return d.toISOString().slice(0, 10);
}

export default function DailyReportPage() {
  const router = useRouter();
  const [date, setDate] = useState<string>(todayIso());
  const [report, setReport] = useState<DailyReport | null>(null);
  const [available, setAvailable] = useState<DailyReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
    });
  }, [router]);

  // Charge la liste des rapports disponibles (une fois)
  useEffect(() => {
    fetchDailyReportsList()
      .then((list) => {
        setAvailable(list);
        // Si rien n'est sélectionné et qu'on a au moins un rapport, ouvrir le plus récent
        if (list.length > 0) {
          setDate((curr) => (curr === todayIso() ? list[0].report_date : curr));
        }
      })
      .catch(() => {
        // pas bloquant
      });
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchDailyReportByDate(date);
      setReport(r); // null si 404 (pas encore généré pour cette date)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleGenerate(force = false) {
    setGenerating(true);
    setError(null);
    try {
      const r = await generateDailyReport(force, date);
      setReport(r);
      // Recharger la liste pour faire apparaître la nouvelle date
      fetchDailyReportsList().then(setAvailable).catch(() => {});
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }

  const isToday = useMemo(() => date === todayIso(), [date]);
  const availableSet = useMemo(
    () => new Set(available.map((a) => a.report_date)),
    [available],
  );

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-4xl space-y-6 px-4 py-6">
        {/* Barre de navigation par date */}
        <section className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="inline-flex items-center gap-1">
            <button
              onClick={() => setDate(shiftDay(date, -1))}
              className="rounded border border-zinc-200 px-2 py-1.5 text-xs text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              aria-label="Jour précédent"
            >
              ◀
            </button>
            <input
              type="date"
              value={date}
              max={todayIso()}
              onChange={(e) => setDate(e.target.value || todayIso())}
              className="rounded border border-zinc-200 bg-white px-2 py-1.5 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
            />
            <button
              onClick={() => setDate(shiftDay(date, 1))}
              disabled={isToday}
              className="rounded border border-zinc-200 px-2 py-1.5 text-xs text-zinc-600 hover:bg-zinc-50 disabled:opacity-30 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              aria-label="Jour suivant"
            >
              ▶
            </button>
          </div>

          <button
            onClick={() => setDate(todayIso())}
            disabled={isToday}
            className="rounded border border-zinc-200 px-2 py-1.5 text-xs text-zinc-600 hover:bg-zinc-50 disabled:opacity-30 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            Aujourd&apos;hui
          </button>

          {available.length > 0 && (
            <select
              value={availableSet.has(date) ? date : ""}
              onChange={(e) => e.target.value && setDate(e.target.value)}
              className="rounded border border-zinc-200 bg-white px-2 py-1.5 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
            >
              <option value="">Rapports disponibles…</option>
              {available.map((r) => (
                <option key={r.report_date} value={r.report_date}>
                  {new Date(r.report_date).toLocaleDateString("fr-FR")}
                </option>
              ))}
            </select>
          )}

          <span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">
            {new Date(date).toLocaleDateString("fr-FR", {
              weekday: "long",
              year: "numeric",
              month: "long",
              day: "numeric",
            })}
          </span>
        </section>

        {/* En-tête + actions */}
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
              Rapport quotidien
            </h1>
            {report && (
              <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
                {report.stats.total_messages} message
                {report.stats.total_messages > 1 ? "s" : ""} · généré par{" "}
                {report.model_used}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            {!report && !loading && (
              <button
                onClick={() => handleGenerate(false)}
                disabled={generating}
                className="rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-900 dark:hover:bg-zinc-200"
              >
                {generating
                  ? "Génération…"
                  : `Générer pour ${new Date(date).toLocaleDateString("fr-FR")}`}
              </button>
            )}
            {report && (
              <button
                onClick={() => handleGenerate(true)}
                disabled={generating}
                className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
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
              Aucun rapport pour le{" "}
              {new Date(date).toLocaleDateString("fr-FR")}.
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
                    ),
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
