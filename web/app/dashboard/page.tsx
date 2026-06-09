"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import { fetchDashboard, DashboardData, DashboardKPIs } from "@/lib/api";

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

export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
    });
  }, [router]);

  useEffect(() => {
    fetchDashboard()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        {loading && <p className="text-sm text-zinc-500">Chargement…</p>}
        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">
            Erreur : {error}
          </div>
        )}

        {data && (
          <>
            {/* KPIs aujourd'hui */}
            <section>
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Aujourd&apos;hui
              </h2>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-7">
                <Kpi label="Messages" today={data.kpis.today.messages} yesterday={data.kpis.yesterday.messages} />
                <Kpi label="Urgents" today={data.kpis.today.urgent} yesterday={data.kpis.yesterday.urgent} accent="urgent" />
                <Kpi label="Hautes" today={data.kpis.today.high} yesterday={data.kpis.yesterday.high} accent="high" />
                <Kpi label="Incidents" today={data.kpis.today.incidents} yesterday={data.kpis.yesterday.incidents} accent="urgent" />
                <Kpi label="Demandes action" today={data.kpis.today.demande_action} yesterday={data.kpis.yesterday.demande_action} accent="high" />
                <Kpi label="Actions requises" today={data.kpis.today.action_required} yesterday={data.kpis.yesterday.action_required} accent="action" />
                <Kpi label="Livraisons" today={data.kpis.today.livraisons} yesterday={data.kpis.yesterday.livraisons} />
              </div>
            </section>

            {/* Urgent items */}
            <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Points d&apos;attention (7 jours)
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
              <Block title="Catégories (7 j)">
                <BarList
                  items={data.categories.map((c) => ({ label: c.category, count: c.count }))}
                />
              </Block>
              <Block title="Priorités (7 j)">
                <BarList
                  items={data.priorities.map((p) => ({
                    label: PRIORITY_LABELS[p.priority] || p.priority,
                    count: p.count,
                    color: PRIORITY_COLORS[p.priority],
                  }))}
                />
              </Block>
              <Block title="Sites mentionnés (7 j)">
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
                Plus actifs (7 j)
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
  today,
  yesterday,
  accent,
}: {
  label: string;
  today: number;
  yesterday: number;
  accent?: "urgent" | "high" | "action";
}) {
  const delta = today - yesterday;
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
      <div className={`mt-1 text-2xl font-semibold ${accentClass}`}>{today}</div>
      <div className="mt-0.5 text-[11px] text-zinc-400">
        hier : {yesterday}{" "}
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
