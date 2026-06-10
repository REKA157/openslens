"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import {
  DailyReport,
  ReportPeriod,
  ReportSummary,
  fetchReport,
  fetchReportsList,
  generateReport,
} from "@/lib/api";

const PERIOD_LABEL: Record<ReportPeriod, string> = {
  day: "Jour",
  week: "Semaine",
  month: "Mois",
};

const PERIOD_LABEL_LONG: Record<ReportPeriod, string> = {
  day: "quotidien",
  week: "hebdomadaire",
  month: "mensuel",
};

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function shift(iso: string, period: ReportPeriod, dir: -1 | 1): string {
  const d = new Date(iso + "T00:00:00Z");
  if (period === "day") d.setUTCDate(d.getUTCDate() + dir);
  else if (period === "week") d.setUTCDate(d.getUTCDate() + 7 * dir);
  else d.setUTCMonth(d.getUTCMonth() + dir);
  return d.toISOString().slice(0, 10);
}

function periodLabelFr(period: ReportPeriod, iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  if (period === "day") {
    return d.toLocaleDateString("fr-FR", {
      weekday: "long",
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  }
  if (period === "week") {
    // On affiche le lundi de la semaine de iso
    const day = d.getUTCDay() || 7; // 1..7
    const monday = new Date(d);
    monday.setUTCDate(d.getUTCDate() - (day - 1));
    const sunday = new Date(monday);
    sunday.setUTCDate(monday.getUTCDate() + 6);
    return `Semaine du ${monday.toLocaleDateString("fr-FR")} au ${sunday.toLocaleDateString("fr-FR")}`;
  }
  return d.toLocaleDateString("fr-FR", { year: "numeric", month: "long" });
}

export default function ReportsPage() {
  const router = useRouter();
  const [period, setPeriod] = useState<ReportPeriod>("day");
  const [date, setDate] = useState<string>(todayIso());
  const [report, setReport] = useState<DailyReport | null>(null);
  const [available, setAvailable] = useState<ReportSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
    });
  }, [router]);

  // Liste disponible pour la période courante
  useEffect(() => {
    fetchReportsList(period)
      .then((list) => {
        setAvailable(list);
        // Si on est sur "aujourd'hui" par défaut et qu'il y a un rapport plus récent
        // qui existe déjà → pointer dessus
        if (list.length > 0 && date === todayIso()) {
          setDate(list[0].report_date);
        }
      })
      .catch(() => {
        setAvailable([]);
      });
    // Volontairement on ne dépend pas de `date` ici : on rafraîchit la liste
    // uniquement à chaque changement de période
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchReport(period, date);
      setReport(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [period, date]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleGenerate(force = false) {
    setGenerating(true);
    setError(null);
    try {
      const r = await generateReport(period, date, force);
      setReport(r);
      fetchReportsList(period).then(setAvailable).catch(() => {});
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
        {/* Tabs Jour/Semaine/Mois + barre date */}
        <section className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="inline-flex overflow-hidden rounded-md border border-zinc-200 dark:border-zinc-700">
            {(["day", "week", "month"] as ReportPeriod[]).map((p) => (
              <button
                key={p}
                onClick={() => setPeriod(p)}
                className={`px-3 py-1.5 text-xs font-medium transition ${
                  period === p
                    ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                    : "bg-white text-zinc-700 hover:bg-zinc-50 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800"
                }`}
              >
                {PERIOD_LABEL[p]}
              </button>
            ))}
          </div>

          <div className="inline-flex items-center gap-1">
            <button
              onClick={() => setDate(shift(date, period, -1))}
              className="rounded border border-zinc-200 px-2 py-1.5 text-xs text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              aria-label="Période précédente"
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
              onClick={() => setDate(shift(date, period, 1))}
              disabled={isToday}
              className="rounded border border-zinc-200 px-2 py-1.5 text-xs text-zinc-600 hover:bg-zinc-50 disabled:opacity-30 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
              aria-label="Période suivante"
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
                  {periodLabelFr(period, r.report_date)}
                </option>
              ))}
            </select>
          )}

          <span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">
            {periodLabelFr(period, date)}
          </span>
        </section>

        {/* En-tête + actions */}
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
              Rapport {PERIOD_LABEL_LONG[period]}
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
                  : `Générer ${PERIOD_LABEL_LONG[period]}`}
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

        {/* Avertissement coût pour mois */}
        {period === "month" && !report && !loading && (
          <div className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
            La génération d&apos;un rapport mensuel peut prendre 30–45 secondes et
            analyse jusqu&apos;à 4000 messages.
          </div>
        )}

        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">
            {error}
          </div>
        )}

        {loading && <p className="text-sm text-zinc-500">Chargement…</p>}

        {!loading && !report && !error && (
          <div className="rounded-lg border border-dashed border-zinc-300 p-8 text-center dark:border-zinc-700">
            <p className="text-sm text-zinc-500">
              Aucun rapport {PERIOD_LABEL_LONG[period]} pour {periodLabelFr(period, date)}.
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
            <section className="rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
              <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">
                {report.content.headline}
              </h2>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
                {report.content.narrative}
              </p>
            </section>

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

            {report.content.open_actions?.length > 0 && (
              <section className="rounded-lg border border-purple-200 bg-purple-50 p-6 dark:border-purple-900 dark:bg-purple-950/20">
                <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-purple-700 dark:text-purple-400">
                  Actions ouvertes
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
