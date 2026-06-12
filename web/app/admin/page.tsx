"use client";

import { useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { Nav } from "@/components/Nav";

type ImportStats = {
  filename?: string;
  batch_id?: string;
  delimiter_detected?: string;
  columns_detected?: Record<string, string>;
  columns_in_csv?: string[];
  rows_parsed?: number;
  rows_skipped_empty?: number;
  rows_inserted?: number;
  rows_skipped_duplicate?: number;
  rows_errors?: number;
  sites_matched?: number;
  sites_unmatched?: string[];
  sample_errors?: string[];
  elapsed_seconds?: number;
  error?: string;
  detail?: string;
};

const FIELD_LABELS: Record<string, string> = {
  external_ref: "N° BL / Référence",
  operation_date: "Date",
  client_name: "Client",
  site_name: "Chantier / Site",
  waste_type: "Type de déchet",
  container_type: "Type de benne",
  quantity: "Quantité",
  unit: "Unité",
  status: "Statut",
  driver: "Chauffeur",
  vehicle: "Véhicule",
  amount_ht: "Montant HT",
};

export default function AdminPage() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<ImportStats | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setLoading(true);
    setStats(null);

    const form = new FormData();
    form.append("file", file);

    try {
      const r = await fetch("/api/admin/import-mkgt", {
        method: "POST",
        body: form,
      });
      const data: ImportStats = await r.json();
      setStats(data);
    } catch (err) {
      setStats({ error: err instanceof Error ? err.message : String(err) });
    } finally {
      setLoading(false);
    }
  }

  const detectedCount = stats?.columns_detected
    ? Object.keys(stats.columns_detected).length
    : 0;

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-3xl px-4 py-8">
        <h1 className="mb-1 text-xl font-semibold text-zinc-900 dark:text-zinc-50">
          Import MKGT
        </h1>
        <p className="mb-6 text-sm text-zinc-500 dark:text-zinc-400">
          Importe un export CSV MKGT dans OpsLens. Les colonnes sont détectées
          automatiquement. Réimporter le même fichier ne crée pas de doublons.
        </p>

        {/* Formulaire upload */}
        <form
          onSubmit={handleSubmit}
          className="rounded-xl border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
        >
          <div
            onClick={() => fileRef.current?.click()}
            className={`cursor-pointer rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors ${
              file
                ? "border-green-400 bg-green-50 dark:border-green-700 dark:bg-green-950/20"
                : "border-zinc-300 hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-600"
            }`}
          >
            <input
              ref={fileRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            {file ? (
              <div>
                <p className="font-medium text-green-700 dark:text-green-400">
                  {file.name}
                </p>
                <p className="mt-1 text-xs text-zinc-500">
                  {(file.size / 1024).toFixed(1)} Ko — cliquer pour changer
                </p>
              </div>
            ) : (
              <div>
                <p className="text-sm text-zinc-600 dark:text-zinc-400">
                  Cliquer pour sélectionner un fichier CSV
                </p>
                <p className="mt-1 text-xs text-zinc-400">
                  Séparateur virgule ou point-virgule — encodage UTF-8 ou latin-1
                </p>
              </div>
            )}
          </div>

          <button
            type="submit"
            disabled={!file || loading}
            className="mt-4 w-full rounded-lg bg-zinc-900 px-4 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40 dark:bg-zinc-50 dark:text-zinc-900"
          >
            {loading ? "Import en cours…" : "Importer"}
          </button>
        </form>

        {/* Résultats */}
        {stats && (
          <div className="mt-6 space-y-4">
            {/* Erreur globale */}
            {(stats.error || stats.detail) && (
              <div className="rounded-lg border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950/20">
                <p className="text-sm font-medium text-red-700 dark:text-red-400">
                  Erreur : {stats.detail || stats.error}
                </p>
              </div>
            )}

            {/* Chiffres clés */}
            {!stats.error && !stats.detail && (
              <>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Kpi
                    label="Lignes importées"
                    value={stats.rows_inserted ?? 0}
                    color="green"
                  />
                  <Kpi
                    label="Colonnes détectées"
                    value={detectedCount}
                    color={detectedCount > 0 ? "blue" : "red"}
                  />
                  <Kpi
                    label="Sites matchés"
                    value={stats.sites_matched ?? 0}
                    color="green"
                  />
                  <Kpi
                    label="Erreurs"
                    value={stats.rows_errors ?? 0}
                    color={(stats.rows_errors ?? 0) > 0 ? "red" : "zinc"}
                  />
                </div>

                <div className="rounded-lg border border-zinc-200 bg-white p-4 text-sm dark:border-zinc-800 dark:bg-zinc-900">
                  <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-zinc-500">
                    <span>Fichier : <strong className="text-zinc-700 dark:text-zinc-300">{stats.filename}</strong></span>
                    <span>Délimiteur : <strong className="text-zinc-700 dark:text-zinc-300">{stats.delimiter_detected === ";" ? "point-virgule" : stats.delimiter_detected === "," ? "virgule" : stats.delimiter_detected}</strong></span>
                    <span>Lignes parsées : <strong className="text-zinc-700 dark:text-zinc-300">{stats.rows_parsed}</strong></span>
                    {(stats.rows_skipped_empty ?? 0) > 0 && (
                      <span>Vides ignorées : <strong className="text-zinc-700 dark:text-zinc-300">{stats.rows_skipped_empty}</strong></span>
                    )}
                    {(stats.rows_skipped_duplicate ?? 0) > 0 && (
                      <span>Doublons ignorés : <strong className="text-zinc-700 dark:text-zinc-300">{stats.rows_skipped_duplicate}</strong></span>
                    )}
                    <span>Durée : <strong className="text-zinc-700 dark:text-zinc-300">{stats.elapsed_seconds}s</strong></span>
                  </div>
                </div>

                {/* Colonnes détectées */}
                {stats.columns_detected && Object.keys(stats.columns_detected).length > 0 && (
                  <div className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
                    <h2 className="mb-3 text-sm font-semibold text-zinc-700 dark:text-zinc-300">
                      Colonnes détectées
                    </h2>
                    <div className="space-y-1">
                      {Object.entries(stats.columns_detected).map(([field, col]) => (
                        <div key={field} className="flex items-center justify-between text-xs">
                          <span className="text-zinc-500">
                            {FIELD_LABELS[field] ?? field}
                          </span>
                          <span className="rounded bg-zinc-100 px-2 py-0.5 font-mono text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                            {col}
                          </span>
                        </div>
                      ))}
                    </div>
                    {/* Colonnes non reconnues */}
                    {stats.columns_in_csv && (
                      () => {
                        const detectedCols = new Set(Object.values(stats.columns_detected ?? {}));
                        const unknown = stats.columns_in_csv!.filter((c) => !detectedCols.has(c));
                        if (unknown.length === 0) return null;
                        return (
                          <div className="mt-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
                            <p className="mb-1 text-xs text-zinc-400">
                              Colonnes non reconnues (stockées dans raw_data) :
                            </p>
                            <div className="flex flex-wrap gap-1">
                              {unknown.map((c) => (
                                <span
                                  key={c}
                                  className="rounded bg-zinc-100 px-1.5 py-0.5 text-[10px] font-mono text-zinc-500 dark:bg-zinc-800"
                                >
                                  {c}
                                </span>
                              ))}
                            </div>
                          </div>
                        );
                      }
                    )()}
                  </div>
                )}

                {/* Sites non matchés */}
                {stats.sites_unmatched && stats.sites_unmatched.length > 0 && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950/20">
                    <h2 className="mb-2 text-sm font-semibold text-amber-800 dark:text-amber-400">
                      Sites non rattachés à un site OpsLens ({stats.sites_unmatched.length})
                    </h2>
                    <p className="mb-2 text-xs text-amber-700 dark:text-amber-500">
                      Ajoute ces noms en tant qu&apos;alias dans la page Sites pour les rattacher automatiquement lors du prochain import.
                    </p>
                    <div className="flex flex-wrap gap-1">
                      {stats.sites_unmatched.map((s) => (
                        <span
                          key={s}
                          className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800 dark:bg-amber-900/40 dark:text-amber-300"
                        >
                          {s}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Erreurs d'import */}
                {stats.sample_errors && stats.sample_errors.length > 0 && (
                  <div className="rounded-lg border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950/20">
                    <h2 className="mb-2 text-sm font-semibold text-red-700 dark:text-red-400">
                      Erreurs sur {stats.rows_errors} ligne(s)
                    </h2>
                    <ul className="space-y-1">
                      {stats.sample_errors.map((e, i) => (
                        <li key={i} className="text-xs font-mono text-red-600 dark:text-red-400">
                          {e}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function Kpi({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: "green" | "blue" | "red" | "zinc";
}) {
  const colorMap = {
    green: "text-green-700 dark:text-green-400",
    blue: "text-blue-700 dark:text-blue-400",
    red: "text-red-700 dark:text-red-400",
    zinc: "text-zinc-500 dark:text-zinc-400",
  };
  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
      <p className={`text-2xl font-bold ${colorMap[color]}`}>{value}</p>
      <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{label}</p>
    </div>
  );
}
