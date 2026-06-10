"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";
import {
  DiscoverProposal,
  Site,
  discoverSites,
  fetchSites,
  saveSites,
} from "@/lib/api";

const REGION_OPTIONS = [
  "",
  "IDF Nord",
  "IDF Sud",
  "Eure-et-Loir",
  "Hauts-de-Seine",
  "Seine-Saint-Denis",
  "Essonne",
  "Yvelines",
  "Autre",
];

export default function SitesPage() {
  const router = useRouter();
  const [sites, setSites] = useState<Site[]>([]);
  const [proposal, setProposal] = useState<DiscoverProposal | null>(null);
  const [discoverStats, setDiscoverStats] = useState<{
    classifications: number;
    rawDistinct: number;
    afterFilter: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [discovering, setDiscovering] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveResult, setSaveResult] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
    });
  }, [router]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await fetchSites();
      setSites(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDiscover() {
    setDiscovering(true);
    setError(null);
    try {
      const res = await discoverSites(2);
      setProposal(res.proposal);
      setDiscoverStats({
        classifications: res.classifications_scanned,
        rawDistinct: res.raw_distinct,
        afterFilter: res.after_filter,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDiscovering(false);
    }
  }

  function importProposal() {
    if (!proposal) return;
    const imported: Site[] = proposal.sites.map((s) => ({
      canonical_name: s.canonical_name,
      aliases: s.aliases,
      region: s.region,
      notes: s.notes ?? null,
      is_active: true,
    }));
    // On conserve les sites déjà saved qui ne sont pas dans la proposition
    const proposedNames = new Set(imported.map((s) => s.canonical_name));
    const kept = sites.filter((s) => !proposedNames.has(s.canonical_name));
    setSites([...imported, ...kept]);
  }

  function addEmpty() {
    setSites((prev) => [
      ...prev,
      {
        canonical_name: "",
        aliases: [],
        region: null,
        notes: null,
        is_active: true,
      },
    ]);
  }

  function update(index: number, patch: Partial<Site>) {
    setSites((prev) =>
      prev.map((s, i) => (i === index ? { ...s, ...patch } : s)),
    );
  }

  function remove(index: number) {
    setSites((prev) => prev.filter((_, i) => i !== index));
  }

  function promoteUncertain(name: string) {
    setSites((prev) => [
      {
        canonical_name: name,
        aliases: [name],
        region: null,
        notes: null,
        is_active: true,
      },
      ...prev,
    ]);
    setProposal((p) =>
      p ? { ...p, uncertain: p.uncertain.filter((u) => u.name !== name) } : p,
    );
  }

  function addAliasToSite(index: number, alias: string) {
    update(index, {
      aliases: [...sites[index].aliases, alias],
    });
    setProposal((p) =>
      p
        ? {
            ...p,
            uncertain: p.uncertain.filter((u) => u.name !== alias),
            noise: p.noise.filter((n) => n.name !== alias),
          }
        : p,
    );
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSaveResult(null);
    try {
      const clean = sites
        .map((s) => ({
          ...s,
          canonical_name: s.canonical_name.trim(),
          aliases: s.aliases.map((a) => a.trim()).filter(Boolean),
        }))
        .filter((s) => s.canonical_name);
      const res = await saveSites(clean, true);
      setSaveResult(
        `Sauvegardé : ${res.upserted} site(s) (suppression de ${res.deleted_before} existants).`,
      );
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        <header className="flex flex-wrap items-center gap-3">
          <div>
            <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
              Sites canoniques
            </h1>
            <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              Liste centrale des sites ADS, avec leurs variantes orthographiques.
              Sert de référentiel pour les futures analyses par site.
            </p>
          </div>
          <div className="ml-auto flex gap-2">
            <button
              onClick={handleDiscover}
              disabled={discovering}
              className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
            >
              {discovering ? "Découverte (30-45 s)…" : "Lancer la découverte automatique"}
            </button>
            <button
              onClick={addEmpty}
              className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
            >
              + Site vide
            </button>
            <button
              onClick={handleSave}
              disabled={saving || sites.length === 0}
              className="rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-900 dark:hover:bg-zinc-200"
            >
              {saving ? "Sauvegarde…" : "Sauvegarder"}
            </button>
          </div>
        </header>

        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-400">
            {error}
          </div>
        )}
        {saveResult && (
          <div className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-400">
            {saveResult}
          </div>
        )}

        {/* Proposition automatique */}
        {proposal && discoverStats && (
          <section className="rounded-lg border border-blue-200 bg-blue-50/50 p-4 dark:border-blue-900 dark:bg-blue-950/20">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-blue-700 dark:text-blue-400">
                Proposition automatique
              </h2>
              <div className="text-xs text-zinc-600 dark:text-zinc-400">
                {discoverStats.classifications} classifs scannées ·{" "}
                {discoverStats.rawDistinct} noms distincts ·{" "}
                {discoverStats.afterFilter} retenus
              </div>
            </div>

            <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-3">
              <div>
                <h3 className="mb-2 text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                  Sites suggérés ({proposal.sites.length})
                </h3>
                <ul className="space-y-1 text-xs">
                  {proposal.sites.map((s) => (
                    <li
                      key={s.canonical_name}
                      className="rounded border border-blue-200 bg-white p-2 dark:border-blue-900 dark:bg-zinc-900"
                    >
                      <div className="font-medium text-zinc-900 dark:text-zinc-100">
                        {s.canonical_name}{" "}
                        <span className="text-zinc-500">
                          · {s.total_occurrences}
                        </span>
                      </div>
                      {s.region && (
                        <div className="text-zinc-500">{s.region}</div>
                      )}
                      <div className="text-zinc-500">
                        {s.aliases.length} alias
                      </div>
                    </li>
                  ))}
                </ul>
                <button
                  onClick={importProposal}
                  className="mt-2 w-full rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
                >
                  Importer dans l&apos;éditeur
                </button>
              </div>

              <div>
                <h3 className="mb-2 text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                  Incertains ({proposal.uncertain.length})
                </h3>
                <p className="mb-2 text-[11px] text-zinc-500 dark:text-zinc-400">
                  Promouvoir en site, ou rattacher à un site existant.
                </p>
                <ul className="space-y-1 text-xs">
                  {proposal.uncertain.map((u) => (
                    <li
                      key={u.name}
                      className="rounded border border-amber-200 bg-white p-2 dark:border-amber-900/50 dark:bg-zinc-900"
                    >
                      <div className="font-medium text-zinc-900 dark:text-zinc-100">
                        {u.name}
                      </div>
                      <div className="text-zinc-500">{u.reason}</div>
                      <div className="mt-1 flex gap-1">
                        <button
                          onClick={() => promoteUncertain(u.name)}
                          className="rounded bg-emerald-600 px-2 py-0.5 text-[10px] text-white hover:bg-emerald-700"
                        >
                          + Site
                        </button>
                        {sites.length > 0 && (
                          <select
                            onChange={(e) => {
                              const idx = Number(e.target.value);
                              if (!Number.isNaN(idx)) addAliasToSite(idx, u.name);
                            }}
                            defaultValue=""
                            className="rounded border border-zinc-200 bg-white px-1 py-0.5 text-[10px] text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
                          >
                            <option value="">Rattacher à…</option>
                            {sites.map((s, i) => (
                              <option key={i} value={i}>
                                {s.canonical_name || "(sans nom)"}
                              </option>
                            ))}
                          </select>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>

              <div>
                <h3 className="mb-2 text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                  Bruit ({proposal.noise.length})
                </h3>
                <p className="mb-2 text-[11px] text-zinc-500 dark:text-zinc-400">
                  Si tu repères un vrai site, clique pour le rattacher.
                </p>
                <ul className="space-y-1 text-xs">
                  {proposal.noise.map((n) => (
                    <li
                      key={n.name}
                      className="rounded border border-zinc-200 bg-white p-2 text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900"
                    >
                      <div className="font-medium text-zinc-700 dark:text-zinc-400">
                        {n.name}
                      </div>
                      <div>{n.reason}</div>
                      <button
                        onClick={() => promoteUncertain(n.name)}
                        className="mt-1 rounded bg-emerald-600 px-2 py-0.5 text-[10px] text-white hover:bg-emerald-700"
                      >
                        + Site
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </section>
        )}

        {/* Éditeur de la liste finale */}
        <section className="rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
          <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-2 dark:border-zinc-800">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Sites enregistrés ({sites.length})
            </h2>
            {loading && (
              <span className="text-xs text-zinc-500">Chargement…</span>
            )}
          </div>

          {sites.length === 0 && !loading && (
            <p className="px-4 py-6 text-sm text-zinc-500">
              Aucun site enregistré. Clique sur &ldquo;Lancer la découverte
              automatique&rdquo; pour obtenir une proposition.
            </p>
          )}

          {sites.length > 0 && (
            <div className="overflow-x-auto">
              <table className="min-w-full text-xs">
                <thead className="bg-zinc-50 text-left text-zinc-500 dark:bg-zinc-950">
                  <tr>
                    <th className="px-3 py-2 font-medium">Nom canonique</th>
                    <th className="px-3 py-2 font-medium">Région</th>
                    <th className="px-3 py-2 font-medium">Aliases (1 par ligne)</th>
                    <th className="px-3 py-2 font-medium">Msg</th>
                    <th className="px-3 py-2 font-medium">Actif</th>
                    <th className="px-3 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {sites.map((s, i) => (
                    <tr
                      key={i}
                      className="border-t border-zinc-100 dark:border-zinc-800"
                    >
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1">
                          <input
                            value={s.canonical_name}
                            onChange={(e) =>
                              update(i, { canonical_name: e.target.value })
                            }
                            className="w-full min-w-[180px] rounded border border-zinc-200 bg-white px-2 py-1 text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                          />
                          {s.id && (
                            <Link
                              href={`/sites/${s.id}`}
                              title="Voir le détail du site"
                              className="shrink-0 rounded border border-zinc-300 px-1.5 py-1 text-[10px] text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                            >
                              →
                            </Link>
                          )}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <select
                          value={s.region || ""}
                          onChange={(e) =>
                            update(i, { region: e.target.value || null })
                          }
                          className="rounded border border-zinc-200 bg-white px-2 py-1 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
                        >
                          {REGION_OPTIONS.map((r) => (
                            <option key={r} value={r}>
                              {r || "—"}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-3 py-2">
                        <textarea
                          value={s.aliases.join("\n")}
                          onChange={(e) =>
                            update(i, {
                              aliases: e.target.value
                                .split("\n")
                                .map((x) => x.trim())
                                .filter(Boolean),
                            })
                          }
                          rows={Math.min(8, Math.max(2, s.aliases.length))}
                          className="w-full min-w-[300px] rounded border border-zinc-200 bg-white px-2 py-1 font-mono text-[11px] text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                        />
                      </td>
                      <td className="px-3 py-2 text-center text-zinc-500">
                        {s.message_count ?? "—"}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <input
                          type="checkbox"
                          checked={s.is_active}
                          onChange={(e) =>
                            update(i, { is_active: e.target.checked })
                          }
                        />
                      </td>
                      <td className="px-3 py-2">
                        <button
                          onClick={() => remove(i)}
                          className="rounded border border-red-300 px-2 py-0.5 text-[10px] text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950/30"
                        >
                          Retirer
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
