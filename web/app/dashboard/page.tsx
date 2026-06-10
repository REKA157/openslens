"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import {
  fetchDashboard,
  DashboardData,
  DashboardPeriod,
} from "@/lib/api";

const PRIORITY_COLORS: Record<string, string> = {
  urgent: "bg-red-500",
  high: "bg-orange-500",
  medium: "bg-yellow-400",
  low: "bg-zinc-400",
};

const PRIORITY_LABELS: Record<string, string> = {
  urgent: "Urgent",
  high: "Haute",
  medium: "Moyenne",
  low: "Basse",
};

const PERIOD_LABEL: Record<DashboardPeriod, string> = {
  day: "Jour",
  week: "Semaine",
  month: "Mois",
};

const PREV_LABEL: Record<DashboardPeriod, string> = {
  day: "veille",
  week: "sem. préc.",
  month: "mois préc.",
};

function todayIso(): string {
  // YYYY-MM-DD en UTC pour rester aligné avec le backend
  return new Date().toISOString().slice(0, 10);
}

function shiftDate(iso: string, period: DashboardPeriod, dir: -1 | 1): string {
  const d = new Date(iso + "T00:00:00Z");
  if (period === "day") d.setUTCDate(d.getUTCDate() + dir);
  else if (period === "week") d.setUTCDate(d.getUTCDate() + 7 * dir);
  else d.setUTCMonth(d.getUTCMonth() + dir);
  return d.toISOString().slice(0, 10);
}

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [period, setPeriod] = useState<DashboardPeriod>("day");
  const [date, setDate] = useState<string>(todayIso());

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
    });
  }, [router]);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchDashboard(date, period)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [date, period]);

  useEffect(() => {
    load();
  }, [load]);

  const isToday = useMemo(() => date === todayIso(), [date]);

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        {/* Barre de navigation temporelle */}
        <section className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="inline-flex overflow-hidden rounded-md border border-zinc-200 dark:border-zinc-700">
            {(["day", "week", "month"] as DashboardPeriod[]).map((p) => (
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
              onClick={() => setDate(shiftDate(date, period, -1))}
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
              onClick={() => setDate(shiftDate(date, period, 1))}
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

          {data && (
            <span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">
              {data.label}
            </span>
          )}
        </section>

        {loading && <p className="text-sm text-zinc-500">Chargement…</p>}
        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">
            Erreur : {error}
          </div>
        )}

        {data && !loading && (
          <>
            {/* KPIs sur la fenêtre choisie */}
            <section>
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                {PERIOD_LABEL[data.period]} — {data.label}
              </h2>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-7">
                <Kpi
                  label="Messages"
                  current={data.kpis.current.messages}
                  previous={data.kpis.previous.messages}
                  prevLabel={PREV_LABEL[data.period]}
                />
                <Kpi
                  label="Urgents"
                  current={data.kpis.current.urgent}
                  previous={data.kpis.previous.urgent}
                  prevLabel={PREV_LABEL[data.period]}
                  accent="urgent"
                />
                <Kpi
                  label="Hautes"
                  current={data.kpis.current.high}
                  previous={data.kpis.previous.high}
                  prevLabel={PREV_LABEL[data.period]}
                  accent="high"
                />
                <Kpi
                  label="Incidents"
                  current={data.kpis.current.incidents}
                  previous={data.kpis.previous.incidents}
                  prevLabel={PREV_LABEL[data.period]}
                  accent="urgent"
                />
                <Kpi
                  label="Demandes action"
                  current={data.kpis.current.demande_action}
                  previous={data.kpis.previous.demande_action}
                  prevLabel={PREV_LABEL[data.period]}
                  accent="high"
                />
                <Kpi
                  label="Actions requises"
                  current={data.kpis.current.action_required}
                  previous={data.kpis.previous.action_required}
                  prevLabel={PREV_LABEL[data.period]}
                  accent="action"
                />
                <Kpi
                  label="Livraisons"
                  current={data.kpis.current.livraisons}
                  previous={data.kpis.previous.livraisons}
                  prevLabel={PREV_LABEL[data.period]}
                />
              </div>
            </section>

            {/* Urgent items */}
            <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Points d&apos;attention ({PERIOD_LABEL[data.period].toLowerCase()})
              </h2>
              {data.urgent_items.length === 0 ? (
                <p className="text-sm text-zinc-500">Rien d&apos;urgent à signaler.</p>
              ) : (
                <ul className="space-y-2">
                  {data.urgent_items.slice(0, 10).map((u) => (
                    <li
                      key={u.message_id}
                      className="flex items-start gap-3 rounded-md border border-zinc-100 p-2 dark:border-zinc-800"
                    >
                      <span
                        className={`mt-1 inline-block h-2 w-2 shrink-0 rounded-full ${
                          PRIORITY_COLORS[u.priority || "low"] || "bg-zinc-300"
                        }`}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                          <span className="font-medium text-zinc-700 dark:text-zinc-300">
                            {u.sender}
                          </span>
                          <span>·</span>
                          <span>{new Date(u.sent_at).toLocaleString("fr-FR")}</span>
                          {u.category && (
                            <span className="rounded bg-blue-50 px-1.5 py-0.5 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">
                              {u.category}
                            </span>
                          )}
                          {u.action_required && (
                            <span className="rounded bg-purple-100 px-1.5 py-0.5 text-purple-700 dark:bg-purple-950/40 dark:text-purple-300">
                              action requise
                            </span>
                          )}
                        </div>
                        <p className="mt-1 text-sm text-zinc-900 dark:text-zinc-100">
                          {u.summary || u.raw_text}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* Trois colonnes : catégories, priorités, sites */}
            <section className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <Block title="Catégories (30 j)">
                <BarList
                  items={data.categories.map((c) => ({ label: c.category, count: c.count }))}
                />
              </Block>
              <Block title="Priorités (30 j)">
                <BarList
                  items={data.priorities.map((p) => ({
                    label: PRIORITY_LABELS[p.priority] || p.priority,
                    count: p.count,
                    color: PRIORITY_COLORS[p.priority],
                  }))}
                />
              </Block>
              <Block title="Sites mentionnés (30 j)">
                {data.top_sites.length === 0 ? (
                  <p className="text-xs text-zinc-500">
                    Aucun site nommément identifié.
                  </p>
                ) : (
                  <BarList
                    items={data.top_sites.map((s) => ({ label: s.site, count: s.count }))}
                  />
                )}
              </Block>
            </section>

            {/* Top senders */}
            <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Plus actifs (30 j)
              </h2>
              <div className="flex flex-wrap gap-2">
                {data.top_senders.map((s) => (
                  <span
                    key={s.sender}
                    className="rounded-full bg-zinc-100 px-3 py-1 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
                  >
                    {s.sender} <span className="text-zinc-400">· {s.count}</span>
                  </span>
                ))}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function Kpi({
  label,
  current,
  previous,
  prevLabel,
  accent,
}: {
  label: string;
  current: number;
  previous: number;
  prevLabel: string;
  accent?: "urgent" | "high" | "action";
}) {
  const delta = current - previous;
  const accentClass =
    accent === "urgent"
      ? "text-red-700 dark:text-red-400"
      : accent === "high"
        ? "text-orange-700 dark:text-orange-400"
        : accent === "action"
          ? "text-purple-700 dark:text-purple-400"
          : "text-zinc-900 dark:text-zinc-50";

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs text-zinc-500 dark:text-zinc-400">{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${accentClass}`}>{current}</div>
      <div className="mt-0.5 text-[11px] text-zinc-400">
        {prevLabel} : {previous}{" "}
        {delta !== 0 && (
          <span className={delta > 0 ? "text-emerald-600" : "text-zinc-500"}>
            ({delta > 0 ? "+" : ""}
            {delta})
          </span>
        )}
      </div>
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {title}
      </h3>
      {children}
    </div>
  );
}

function BarList({
  items,
}: {
  items: { label: string; count: number; color?: string }[];
}) {
  if (items.length === 0) {
    return <p className="text-xs text-zinc-500">Aucune donnée.</p>;
  }
  const max = Math.max(...items.map((i) => i.count));
  return (
    <ul className="space-y-1.5">
      {items.map((i) => {
        const width = max > 0 ? (i.count / max) * 100 : 0;
        return (
          <li key={i.label}>
            <div className="mb-0.5 flex items-center justify-between text-xs">
              <span className="truncate text-zinc-700 dark:text-zinc-300">
                {i.label}
              </span>
              <span className="ml-2 text-zinc-500">{i.count}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded bg-zinc-100 dark:bg-zinc-800">
              <div
                className={`h-full rounded ${i.color || "bg-zinc-500"}`}
                style={{ width: `${width}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
