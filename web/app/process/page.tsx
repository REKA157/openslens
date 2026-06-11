"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import {
  CriticalHours,
  DeadThreads,
  ProcessAnalysisData,
  RepeatedCluster,
  RepeatedRequests,
  ResponseTimes,
  fetchProcessAnalysis,
} from "@/lib/api";

export default function ProcessPage() {
  const router = useRouter();
  const [data, setData] = useState<ProcessAnalysisData | null>(null);
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
      const d = await fetchProcessAnalysis();
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
            Analyse des processus
          </h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
            Mesures opérationnelles pour identifier les inefficiences du
            quotidien ADS.
          </p>
          {data && (
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              {data.messages_scanned} messages · {data.classifications_loaded}{" "}
              classifications · {data.sites_count} sites
            </p>
          )}
        </header>

        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">
            {error}
          </div>
        )}
        {loading && (
          <p className="text-sm text-zinc-500">Calcul des indicateurs…</p>
        )}

        {data && !loading && (
          <>
            <ResponseTimesSection data={data.response_times} />
            <DeadThreadsSection data={data.dead_threads} />
            <RepeatedSection data={data.repeated_requests} />
            <CriticalHoursSection data={data.critical_hours} />
          </>
        )}
      </main>
    </div>
  );
}

// ---------- Délais de réponse ----------

function ResponseTimesSection({ data }: { data: ResponseTimes }) {
  const resolutionRate =
    data.total_requests > 0
      ? Math.round((data.matched_resolutions / data.total_requests) * 100)
      : 0;
  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Délais de réponse
      </h2>
      <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
        Temps entre une demande nécessitant action et sa première résolution
        détectée (fenêtre {data.window_hours}h).
      </p>

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi label="Demandes" value={data.total_requests} />
        <Kpi
          label="Résolues"
          value={`${data.matched_resolutions} (${resolutionRate}%)`}
          accent="success"
        />
        <Kpi label="Non résolues" value={data.unresolved_count} accent="warning" />
        <Kpi
          label="Délai médian"
          value={
            data.global_stats.median != null
              ? `${data.global_stats.median} h`
              : "—"
          }
          accent="info"
        />
      </div>

      {data.by_site.length > 0 && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Par site (triés par délai médian décroissant)
          </h3>
          <div className="mt-2 overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead className="text-left text-zinc-500">
                <tr>
                  <th className="py-2 pr-3">Site</th>
                  <th className="py-2 pr-3">Demandes résolues</th>
                  <th className="py-2 pr-3">Médian (h)</th>
                  <th className="py-2 pr-3">P90 (h)</th>
                  <th className="py-2 pr-3">Max (h)</th>
                </tr>
              </thead>
              <tbody>
                {data.by_site.map((row) => (
                  <tr
                    key={row.site_id}
                    className="border-t border-zinc-100 dark:border-zinc-800"
                  >
                    <td className="py-2 pr-3">
                      <Link
                        href={`/sites/${row.site_id}`}
                        className="font-medium text-zinc-900 hover:underline dark:text-zinc-50"
                      >
                        {row.site_name}
                      </Link>
                    </td>
                    <td className="py-2 pr-3 text-zinc-700 dark:text-zinc-300">
                      {row.n}
                    </td>
                    <td className="py-2 pr-3 font-medium text-zinc-900 dark:text-zinc-50">
                      {row.median ?? "—"}
                    </td>
                    <td className="py-2 pr-3 text-zinc-600 dark:text-zinc-400">
                      {row.p90 ?? "—"}
                    </td>
                    <td className="py-2 pr-3 text-zinc-500">
                      {row.max ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {data.by_category.length > 0 && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Par catégorie de demande
          </h3>
          <div className="mt-2 flex flex-wrap gap-2">
            {data.by_category.slice(0, 12).map((row) => (
              <span
                key={row.category}
                className="rounded bg-zinc-100 px-2 py-1 text-xs dark:bg-zinc-800"
              >
                <span className="font-medium">{row.category}</span> :{" "}
                <span className="text-zinc-700 dark:text-zinc-300">
                  {row.median ?? "—"}h
                </span>{" "}
                <span className="text-zinc-400">({row.n})</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// ---------- Fils morts ----------

function DeadThreadsSection({ data }: { data: DeadThreads }) {
  return (
    <section
      className={`rounded-lg border-2 p-4 ${
        data.dead_count > 5
          ? "border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/20"
          : data.dead_count > 0
            ? "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/20"
            : "border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900"
      }`}
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Fils morts ({data.dead_count})
      </h2>
      <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
        Demandes signalées comme nécessitant action il y a plus de{" "}
        {data.min_age_hours}h, sans résolution détectée. Probablement oubliées.
      </p>

      {data.dead_count === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">
          Aucun fil mort détecté. 🎯
        </p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="font-medium text-zinc-700 dark:text-zinc-300">
              Par site :
            </span>
            {data.by_site.slice(0, 8).map((s) => (
              <span
                key={s.site_name}
                className="rounded bg-white/60 px-2 py-0.5 dark:bg-zinc-900/60"
              >
                {s.site_name} :{" "}
                <span className="font-semibold">{s.count}</span>
              </span>
            ))}
          </div>

          <ul className="mt-4 space-y-2">
            {data.items.slice(0, 15).map((item) => (
              <li
                key={item.message_id}
                className="rounded-md border border-zinc-100 bg-white p-3 text-sm dark:border-zinc-800 dark:bg-zinc-900"
              >
                <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                  <span className="font-semibold text-red-700 dark:text-red-400">
                    {item.age_days} j
                  </span>
                  <span>·</span>
                  <Link
                    href={`/sites/${item.site_id}`}
                    className="font-medium text-zinc-700 hover:underline dark:text-zinc-300"
                  >
                    {item.site_name}
                  </Link>
                  <span>·</span>
                  <span>{item.sender}</span>
                  {item.category && (
                    <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] dark:bg-zinc-800">
                      {item.category}
                    </span>
                  )}
                  {item.priority && (
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] ${
                        item.priority === "urgent"
                          ? "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300"
                          : item.priority === "high"
                            ? "bg-orange-100 text-orange-700 dark:bg-orange-950/40 dark:text-orange-300"
                            : "bg-zinc-100 dark:bg-zinc-800"
                      }`}
                    >
                      {item.priority}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-zinc-900 dark:text-zinc-100">
                  {item.summary}
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

// ---------- Demandes répétées ----------

function RepeatedSection({ data }: { data: RepeatedRequests }) {
  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Demandes répétées ({data.clusters_count})
      </h2>
      <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
        Mêmes types de demandes revenant ≥ {data.min_repetitions} fois sur{" "}
        {data.window_days} jours sur le même site — signal probable de process
        cassé ou de demandes oubliées.
      </p>

      {data.clusters_count === 0 ? (
        <p className="mt-3 text-sm text-zinc-500">
          Aucun pattern de répétition détecté.
        </p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="font-medium text-zinc-700 dark:text-zinc-300">
              Par site :
            </span>
            {data.by_site_summary.slice(0, 8).map((s) => (
              <span
                key={s.site_name}
                className="rounded bg-zinc-100 px-2 py-0.5 dark:bg-zinc-800"
              >
                {s.site_name} :{" "}
                <span className="font-semibold">{s.repeated_requests}</span>
              </span>
            ))}
          </div>

          <div className="mt-4 space-y-2">
            {data.clusters.slice(0, 12).map((c, i) => (
              <RepeatedClusterCard key={i} cluster={c} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function RepeatedClusterCard({ cluster }: { cluster: RepeatedCluster }) {
  return (
    <details className="rounded-md border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
      <summary className="cursor-pointer list-none px-3 py-2 text-sm">
        <span className="font-semibold text-zinc-900 dark:text-zinc-50">
          {cluster.site_name}
        </span>{" "}
        <span className="text-zinc-500">·</span>{" "}
        <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] dark:bg-zinc-800">
          {cluster.category}
        </span>{" "}
        <span className="text-zinc-500">·</span>{" "}
        <span className="font-medium text-amber-700 dark:text-amber-400">
          {cluster.count} fois en {Math.round(cluster.span_hours / 24)} j
        </span>
      </summary>
      <div className="border-t border-zinc-100 px-3 py-2 dark:border-zinc-800">
        <ul className="space-y-1 text-xs">
          {cluster.examples.map((ex, i) => (
            <li
              key={i}
              className="flex items-start gap-2 text-zinc-700 dark:text-zinc-300"
            >
              <span className="text-zinc-400">{ex.date}</span>
              <span>{ex.summary}</span>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}

// ---------- Heures critiques ----------

function CriticalHoursSection({ data }: { data: CriticalHours }) {
  const max = useMemo(() => {
    let m = 0;
    for (const row of data.heatmap) {
      for (const v of row) m = Math.max(m, v);
    }
    return m;
  }, [data.heatmap]);

  return (
    <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Heures critiques
      </h2>
      <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
        Distribution sur 7 jours × 24 heures des messages urgents ou nécessitant
        action ({data.total_critical_messages} messages au total).
      </p>

      {/* Heatmap */}
      <div className="mt-3 overflow-x-auto">
        <table className="text-[10px]">
          <thead>
            <tr>
              <th className="px-1"></th>
              {Array.from({ length: 24 }, (_, h) => (
                <th
                  key={h}
                  className="px-1 text-center font-normal text-zinc-500"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.heatmap.map((row, dow) => (
              <tr key={dow}>
                <td className="pr-2 font-medium text-zinc-600 dark:text-zinc-400">
                  {data.day_labels[dow]}
                </td>
                {row.map((count, hour) => {
                  const intensity = max > 0 ? count / max : 0;
                  return (
                    <td
                      key={hour}
                      className="border border-zinc-50 dark:border-zinc-900"
                      style={{
                        backgroundColor:
                          count === 0
                            ? "transparent"
                            : `rgba(220, 38, 38, ${0.15 + intensity * 0.7})`,
                        width: "18px",
                        height: "18px",
                      }}
                      title={`${data.day_labels[dow]} ${hour}h : ${count} message(s)`}
                    />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.top_buckets.length > 0 && (
        <div className="mt-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Top 10 créneaux les plus tendus
          </h3>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            {data.top_buckets.map((b, i) => (
              <span
                key={i}
                className="rounded bg-red-100 px-2 py-1 text-red-800 dark:bg-red-950/40 dark:text-red-300"
              >
                {b.day} {b.hour}h :{" "}
                <span className="font-semibold">{b.count}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

// ---------- UI helpers ----------

function Kpi({
  label,
  value,
  accent,
}: {
  label: string;
  value: number | string;
  accent?: "success" | "warning" | "info";
}) {
  const accentCls =
    accent === "success"
      ? "text-emerald-700 dark:text-emerald-400"
      : accent === "warning"
        ? "text-amber-700 dark:text-amber-400"
        : accent === "info"
          ? "text-blue-700 dark:text-blue-400"
          : "text-zinc-900 dark:text-zinc-50";
  return (
    <div className="rounded-md border border-zinc-200 bg-white p-2 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-[11px] text-zinc-500 dark:text-zinc-400">
        {label}
      </div>
      <div className={`mt-1 text-lg font-semibold ${accentCls}`}>{value}</div>
    </div>
  );
}
