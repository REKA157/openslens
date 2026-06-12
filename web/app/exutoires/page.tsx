"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";

type MonthCell = { month: number; label: string; contractual: number; real: number | null; pct: number | null };
type ExutoireRow = {
  id: string;
  name: string;
  parent_group: string | null;
  contractual_annual_min: number;
  contractual_annual_max: number | null;
  real_source: string;
  cumul_real: number;
  pct_annual: number | null;
  projection_annual: number;
  pct_projection: number | null;
  delta_projection: number;
  status: string;
  months: MonthCell[];
};
type Tracking = {
  year: number;
  has_config: boolean;
  has_data: boolean;
  empty_hint: string | null;
  source_used: string;
  totals: {
    contractual_annual: number; cumul_real: number; pct_annual: number | null;
    projection_annual: number; delta_projection: number;
  };
  exutoires: ExutoireRow[];
};

function fmt(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("fr-FR", { maximumFractionDigits: 0 });
}

const STATUS: Record<string, { label: string; cls: string }> = {
  ok: { label: "Conforme", cls: "bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-300" },
  sur_objectif: { label: "Au-dessus du max", cls: "bg-blue-100 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300" },
  sous_objectif: { label: "Sous objectif", cls: "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300" },
  critique: { label: "Critique", cls: "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300" },
};

export default function ExutoiresPage() {
  const router = useRouter();
  const [data, setData] = useState<Tracking | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const year = 2026;

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
    });
  }, [router]);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetch(`/api/exutoires?year=${year}`, { cache: "no-store" })
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [year]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            Exutoires — apports de déchets ultimes {year}
          </h1>
          {data && (
            <span className="text-xs text-zinc-500">
              Source réel : {data.source_used === "mkgt" ? "MKGT" : data.source_used === "manual" ? "saisie" : "—"}
            </span>
          )}
        </div>

        {loading && <p className="text-sm text-zinc-500">Chargement…</p>}
        {error && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">Erreur : {error}</div>}

        {!loading && data && !data.has_config && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300">
            {data.empty_hint || "Aucun exutoire configuré."}
          </div>
        )}

        {!loading && data && data.has_config && (
          <>
            {/* Totaux */}
            <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Kpi label="Contractuel annuel" value={`${fmt(data.totals.contractual_annual)} t`} />
              <Kpi label="Réel cumulé" value={`${fmt(data.totals.cumul_real)} t`} sub={data.totals.pct_annual != null ? `${data.totals.pct_annual}% de l'annuel` : undefined} accent="emerald" />
              <Kpi label="Projection fin d'année" value={`${fmt(data.totals.projection_annual)} t`} accent="indigo" />
              <Kpi label="Delta projeté" value={`${data.totals.delta_projection > 0 ? "+" : ""}${fmt(data.totals.delta_projection)} t`} accent={data.totals.delta_projection < 0 ? "red" : "emerald"} />
            </section>

            {/* Tableau par exutoire */}
            <section className="overflow-x-auto rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-zinc-500">
                    <th className="pb-2">Exutoire</th>
                    <th className="pb-2 text-right">Contrat min</th>
                    <th className="pb-2 text-right">Réel cumulé</th>
                    <th className="pb-2 text-right">Projection</th>
                    <th className="pb-2 text-right">% proj.</th>
                    <th className="pb-2 text-right">Delta</th>
                    <th className="pb-2 pl-3">Statut</th>
                  </tr>
                </thead>
                <tbody>
                  {data.exutoires.map((e) => {
                    const st = STATUS[e.status] || STATUS.ok;
                    return (
                      <tr key={e.id} className="border-t border-zinc-100 dark:border-zinc-800">
                        <td className="py-1.5 font-medium text-zinc-800 dark:text-zinc-200">
                          {e.name}
                          {e.parent_group && <span className="ml-1 text-[10px] text-zinc-400">({e.parent_group})</span>}
                        </td>
                        <td className="py-1.5 text-right">{fmt(e.contractual_annual_min)}</td>
                        <td className="py-1.5 text-right text-emerald-700 dark:text-emerald-400">{fmt(e.cumul_real)}</td>
                        <td className="py-1.5 text-right text-indigo-700 dark:text-indigo-400">{fmt(e.projection_annual)}</td>
                        <td className="py-1.5 text-right">{e.pct_projection != null ? `${e.pct_projection}%` : "—"}</td>
                        <td className={`py-1.5 text-right ${e.delta_projection < 0 ? "text-red-600" : "text-emerald-600"}`}>
                          {e.delta_projection > 0 ? "+" : ""}{fmt(e.delta_projection)}
                        </td>
                        <td className="py-1.5 pl-3">
                          <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${st.cls}`}>{st.label}</span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>

            {/* Détail mensuel par exutoire */}
            {data.exutoires.map((e) => (
              <section key={e.id} className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
                <h3 className="mb-2 text-sm font-semibold text-zinc-700 dark:text-zinc-300">{e.name}</h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-zinc-500">
                        <th className="pb-1 text-left"></th>
                        {e.months.map((m) => <th key={m.month} className="pb-1 text-right">{m.label}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      <tr className="border-t border-zinc-100 dark:border-zinc-800">
                        <td className="py-1 text-left text-zinc-500">Contractuel</td>
                        {e.months.map((m) => <td key={m.month} className="py-1 text-right text-zinc-500">{fmt(m.contractual)}</td>)}
                      </tr>
                      <tr>
                        <td className="py-1 text-left text-zinc-700 dark:text-zinc-300">Réel</td>
                        {e.months.map((m) => <td key={m.month} className="py-1 text-right text-zinc-800 dark:text-zinc-200">{m.real == null ? "—" : fmt(m.real)}</td>)}
                      </tr>
                      <tr>
                        <td className="py-1 text-left text-zinc-500">% Obj.</td>
                        {e.months.map((m) => (
                          <td key={m.month} className={`py-1 text-right ${m.pct == null ? "text-zinc-400" : m.pct >= 100 ? "text-emerald-600" : m.pct >= 70 ? "text-amber-600" : "text-red-600"}`}>
                            {m.pct == null ? "—" : `${Math.round(m.pct)}%`}
                          </td>
                        ))}
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>
            ))}
          </>
        )}
      </main>
    </div>
  );
}

function Kpi({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: "emerald" | "indigo" | "red" }) {
  const cls = accent === "emerald" ? "text-emerald-700 dark:text-emerald-400"
    : accent === "indigo" ? "text-indigo-700 dark:text-indigo-400"
    : accent === "red" ? "text-red-700 dark:text-red-400"
    : "text-zinc-900 dark:text-zinc-50";
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs text-zinc-500 dark:text-zinc-400">{label}</div>
      <div className={`mt-1 text-xl font-bold ${cls}`}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-zinc-400">{sub}</div>}
    </div>
  );
}
