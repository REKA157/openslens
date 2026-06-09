"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";

type Row = {
  id: string;
  external_message_id: string;
  sender_phone: string | null;
  sender_display_name: string | null;
  message_type: string;
  raw_text: string | null;
  sent_at: string;
  message_classifications:
    | {
        business_category: string | null;
        priority: string | null;
        summary: string | null;
        action_required: boolean;
        confidence: number | null;
        requires_human_review: boolean;
      }[]
    | null;
  whatsapp_media:
    | {
        id: string;
        media_type: string;
        storage_path: string | null;
        mime_type: string | null;
      }[]
    | null;
};

const PRIORITY_COLORS: Record<string, string> = {
  urgent: "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  high: "bg-orange-100 text-orange-700 dark:bg-orange-950/40 dark:text-orange-300",
  medium: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950/40 dark:text-yellow-300",
  low: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
};

export default function MessagesPage() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterCategory, setFilterCategory] = useState<string>("all");
  const [filterPriority, setFilterPriority] = useState<string>("all");

  // Auth guard
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) {
        router.replace("/login");
      }
    });
  }, [router]);

  const fetchRows = useCallback(async () => {
    setLoading(true);
    const { data, error } = await supabase
      .from("whatsapp_messages")
      .select(
        `
        id,
        external_message_id,
        sender_phone,
        sender_display_name,
        message_type,
        raw_text,
        sent_at,
        message_classifications (
          business_category,
          priority,
          summary,
          action_required,
          confidence,
          requires_human_review
        ),
        whatsapp_media (
          id,
          media_type,
          storage_path,
          mime_type
        )
        `
      )
      .order("sent_at", { ascending: false })
      .limit(100);

    if (error) {
      console.error("Fetch messages error:", error);
      setRows([]);
    } else {
      setRows(data as Row[]);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchRows();
  }, [fetchRows]);

  async function handleLogout() {
    await supabase.auth.signOut();
    router.replace("/login");
  }

  const filtered = rows.filter((r) => {
    const c = r.message_classifications?.[0];
    if (filterCategory !== "all" && c?.business_category !== filterCategory) return false;
    if (filterPriority !== "all" && c?.priority !== filterPriority) return false;
    return true;
  });

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b border-zinc-200 bg-white/80 backdrop-blur dark:border-zinc-800 dark:bg-black/80">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
          <div>
            <h1 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
              OpsLens — Journal
            </h1>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Groupe ADS Multi Sites · {filtered.length} message{filtered.length > 1 ? "s" : ""}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={fetchRows}
              className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
            >
              Rafraîchir
            </button>
            <button
              onClick={handleLogout}
              className="rounded-md border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
            >
              Déconnexion
            </button>
          </div>
        </div>

        {/* Filtres */}
        <div className="mx-auto flex max-w-7xl flex-wrap gap-2 border-t border-zinc-200 px-4 py-2 dark:border-zinc-800">
          <select
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
            className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
          >
            <option value="all">Toutes catégories</option>
            <option value="incident">Incident</option>
            <option value="urgence">Urgence</option>
            <option value="demande_action">Demande d'action</option>
            <option value="validation">Validation</option>
            <option value="livraison">Livraison</option>
            <option value="intervention">Intervention</option>
            <option value="panne">Panne</option>
            <option value="retard">Retard</option>
            <option value="document_recu">Document reçu</option>
            <option value="preuve_photo">Preuve photo</option>
            <option value="info">Info</option>
          </select>

          <select
            value={filterPriority}
            onChange={(e) => setFilterPriority(e.target.value)}
            className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
          >
            <option value="all">Toutes priorités</option>
            <option value="urgent">Urgent</option>
            <option value="high">Haute</option>
            <option value="medium">Moyenne</option>
            <option value="low">Basse</option>
          </select>
        </div>
      </header>

      {/* Liste */}
      <main className="mx-auto max-w-7xl px-4 py-6">
        {loading ? (
          <p className="text-sm text-zinc-500">Chargement…</p>
        ) : filtered.length === 0 ? (
          <p className="text-sm text-zinc-500">Aucun message correspondant aux filtres.</p>
        ) : (
          <ul className="space-y-3">
            {filtered.map((r) => (
              <MessageCard key={r.id} row={r} />
            ))}
          </ul>
        )}
      </main>
    </div>
  );
}

function MessageCard({ row }: { row: Row }) {
  const c = row.message_classifications?.[0];
  const media = row.whatsapp_media?.[0];
  const priorityClass = c?.priority
    ? PRIORITY_COLORS[c.priority] ?? PRIORITY_COLORS.low
    : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-500";

  const [signedUrl, setSignedUrl] = useState<string | null>(null);

  useEffect(() => {
    if (media?.storage_path && media.media_type === "image") {
      supabase.storage
        .from("Media")
        .createSignedUrl(media.storage_path, 300)
        .then(({ data }) => setSignedUrl(data?.signedUrl ?? null));
    }
  }, [media?.storage_path, media?.media_type]);

  return (
    <li className="rounded-lg border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
            <span className="font-medium text-zinc-700 dark:text-zinc-300">
              {row.sender_display_name || row.sender_phone || "?"}
            </span>
            <span>·</span>
            <span>{new Date(row.sent_at).toLocaleString("fr-FR")}</span>
            <span>·</span>
            <span className="rounded bg-zinc-100 px-1.5 py-0.5 dark:bg-zinc-800">
              {row.message_type}
            </span>
            {c?.business_category && (
              <span className="rounded bg-blue-50 px-1.5 py-0.5 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">
                {c.business_category}
              </span>
            )}
            {c?.priority && (
              <span className={`rounded px-1.5 py-0.5 font-medium ${priorityClass}`}>
                {c.priority}
              </span>
            )}
            {c?.action_required && (
              <span className="rounded bg-purple-100 px-1.5 py-0.5 text-purple-700 dark:bg-purple-950/40 dark:text-purple-300">
                action requise
              </span>
            )}
            {c?.requires_human_review && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
                revue humaine
              </span>
            )}
          </div>

          {row.raw_text && (
            <p className="mt-2 whitespace-pre-wrap text-sm text-zinc-900 dark:text-zinc-100">
              {row.raw_text}
            </p>
          )}

          {c?.summary && c.summary !== row.raw_text && (
            <p className="mt-2 text-xs italic text-zinc-600 dark:text-zinc-400">
              📋 {c.summary}
            </p>
          )}

          {c?.confidence !== null && c?.confidence !== undefined && (
            <p className="mt-1 text-[10px] text-zinc-400">
              confiance: {(c.confidence * 100).toFixed(0)}%
            </p>
          )}
        </div>

        {signedUrl && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={signedUrl}
            alt="média"
            className="h-24 w-24 shrink-0 rounded-md object-cover"
          />
        )}
      </div>
    </li>
  );
}
