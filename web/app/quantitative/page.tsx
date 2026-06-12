"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";

type Period = "day" | "week" | "month";
const PERIOD_LABEL: Record<Period, string> = { day: "Jour", week: "Semaine", month: "Mois" };

type SiteRow = {
  site_id: string;
  site_name: string;
  tonnage: number;
  amount_ht: number;
  operations: number;
  by_waste_type?: { waste_type: string; tonnage: number }[];
};
type QuantData = {
  window: [string, string];
  has_data: boolean;
  empty_hint: string | null;
  by_site: SiteRow[];
  totals: { tonnage: number; amount_ht: number; operations: number };
  unmatched?: { operations: number; tonnage: number; amount_ht: number };
};
type ReconData = {
  counts: { wa_documents: number; mkgt_operations: number; matched: number; wa_only: number; mkgt_only: number };
  wa_only: { reference: string | null; site_name: string | null; date: string; tonnage: number | null; amount: number | null }[];
  mkgt_only: { external_ref: string | null; site_name: string | null; operation_date: string | null; tonnage: number | null; amount_ht: number | null }[];
};
type ForecastSite = {
  site_id: string;
  site_name: string;
  method: string;
  summary: { history_total_tonnage: number; expected_total_tonnage: number };
};
type ForecastData = { has_data: boolean; empty_hint: string | null; horizon_days: number; sites: ForecastSite[] };

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function fmt(n: number): string {
  return n.toLocaleString("fr-FR", { maximumFractionDigits: 1 });
}

export default function QuantitativePage() {
  const router = useRouter();
  const [period, setPeriod] = useState<Period>("month");
  const [date] = useState<string>(todayIso());
  const [quant, setQuant] = useState<QuantData | null>(null);
  const [recon, setRecon] = useState<ReconData | null>(null);
  const [forecast, setForecast] = useState<ForecastData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
    });
  }, [router]);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      fetch(`/api/quantitative?period=${period}&date=${date}`, { cache: "no-store" }).then((r) => r.json()),
      fetch(`/api/reconciliation?period=${period}&date=${date}`, { cache: "no-store" }).then((r) => r.json()),
      fetch(`/api/forecast-tonnage?horizon_days=30`, { cache: "no-store" }).then((r) => r.json()),
    ])
      .then(([q, rc, fc]) => {
        setQuant(q);
        setRecon(rc);
        setForecast(fc);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [period, date]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        <section className="flex flex-wrap items-center gap-3 rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
          <h1 className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">Quantitatif — tonnages & €</h1>
          <div className="inline-flex overflow-hidden rounded-md border border-zinc-200 dark:border-zinc-700">
            {(["day", "week", "month"] as Period[]).map((p) => (
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
          {quant && (
            <span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">
              {quant.window?.[0]} → {quant.window?.[1]}
            </span>
          )}
        </section>

        {loading && <p className="text-sm text-zinc-500">Chargement…</p>}
        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">
            Erreur : {error}
          </div>
        )}

        {!loading && quant && (
          <>
            {/* Bandeau "pas de données" */}
            {!quant.has_data && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300">
                {quant.empty_hint || "Aucune donnée quantitative sur la période."}
              </div>
            )}

            {/* KPIs */}
            <section className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <BigKpi label="Tonnage collecté" value={`${fmt(quant.totals.tonnage)} t`} accent="emerald" />
              <BigKpi label="Chiffre d'affaires HT" value={`${fmt(quant.totals.amount_ht)} €`} accent="indigo" />
              <BigKpi label="Opérations" value={fmt(quant.totals.operations)} />
            </section>

            {/* Par site */}
            {quant.by_site.length > 0 && (
              <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
                <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Par site
                </h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-zinc-500">
                        <th className="pb-2">Site</th>
                        <th className="pb-2 text-right">Tonnage</th>
                        <th className="pb-2 text-right">€ HT</th>
                        <th className="pb-2 text-right">Op.</th>
                        <th className="pb-2 pl-3">Top matières</th>
                      </tr>
                    </thead>
                    <tbody>
                      {quant.by_site.map((s) => (
                        <tr key={s.site_id} className="border-t border-zinc-100 dark:border-zinc-800">
                          <td className="py-1.5 font-medium text-zinc-800 dark:text-zinc-200">{s.site_name}</td>
                          <td className="py-1.5 text-right text-emerald-700 dark:text-emerald-400">{fmt(s.tonnage)} t</td>
                          <td className="py-1.5 text-right text-indigo-700 dark:text-indigo-400">{fmt(s.amount_ht)} €</td>
                          <td className="py-1.5 text-right text-zinc-500">{s.operations}</td>
                          <td className="py-1.5 pl-3 text-xs text-zinc-500">
                            {(s.by_waste_type || []).slice(0, 3).map((w) => `${w.waste_type} (${fmt(w.tonnage)}t)`).join(", ")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            {/* Prévision tonnage */}
            <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Prévision tonnage (30 j)
              </h2>
              {forecast && forecast.has_data ? (
                <ul className="space-y-1.5">
                  {forecast.sites.map((f) => (
                    <li key={f.site_id} className="flex items-center justify-between text-sm">
                      <span className="text-zinc-700 dark:text-zinc-300">{f.site_name}</span>
                      <span className="text-emerald-700 dark:text-emerald-400">
                        ≈ {fmt(f.summary.expected_total_tonnage)} t
                        <span className="ml-2 text-[10px] text-zinc-400">{f.method}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-zinc-500">{forecast?.empty_hint || "Pas assez de données MKGT pour prévoir."}</p>
              )}
            </section>

            {/* Réconciliation */}
            <section className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Réconciliation WhatsApp ↔ MKGT
              </h2>
              {recon ? (
                <>
                  <div className="mb-3 flex flex-wrap gap-2 text-xs">
                    <Pill label={`${recon.counts.matched} rapprochés`} tone="green" />
                    <Pill label={`${recon.counts.wa_only} sur WhatsApp seul`} tone="amber" />
                    <Pill label={`${recon.counts.mkgt_only} dans MKGT seul`} tone="zinc" />
                  </div>
                  {recon.counts.wa_only > 0 ? (
                    <div>
                      <p className="mb-2 text-xs font-medium text-amber-700 dark:text-amber-400">
                        ⚠ Bons vus sur WhatsApp mais absents de MKGT (risque d&apos;oubli de saisie/facturation) :
                      </p>
                      <ul className="space-y-1 text-xs text-zinc-600 dark:text-zinc-400">
                        {recon.wa_only.slice(0, 15).map((w, i) => (
                          <li key={i} className="flex flex-wrap gap-x-3 border-t border-zinc-100 py-1 dark:border-zinc-800">
                            <span>{w.date}</span>
                            {w.reference && <span>Réf {w.reference}</span>}
                            {w.site_name && <span>· {w.site_name}</span>}
                            {w.tonnage != null && <span>· {fmt(w.tonnage)} t</span>}
                            {w.amount != null && <span>· {fmt(w.amount)} €</span>}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : (
                    <p className="text-xs text-zinc-500">
                      Aucun écart détecté (ou données insuffisantes — alimente MKGT et les documents WhatsApp pour activer).
                    </p>
                  )}
                </>
              ) : (
                <p className="text-xs text-zinc-500">Indisponible.</p>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function BigKpi({ label, value, accent }: { label: string; value: string; accent?: "emerald" | "indigo" }) {
  const cls =
    accent === "emerald"
      ? "text-emerald-700 dark:text-emerald-400"
      : accent === "indigo"
        ? "text-indigo-700 dark:text-indigo-400"
        : "text-zinc-900 dark:text-zinc-50";
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="text-xs text-zinc-500 dark:text-zinc-400">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${cls}`}>{value}</div>
    </div>
  );
}

function Pill({ label, tone }: { label: string; tone: "green" | "amber" | "zinc" }) {
  const cls = {
    green: "bg-green-100 text-green-700 dark:bg-green-950/40 dark:text-green-300",
    amber: "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
    zinc: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
  }[tone];
  return <span className={`rounded-full px-2.5 py-1 font-medium ${cls}`}>{label}</span>;
}
