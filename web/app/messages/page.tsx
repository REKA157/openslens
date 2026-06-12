"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { Nav } from "@/components/Nav";

type Message = {
  id: string;
  external_message_id: string;
  sender_phone: string | null;
  sender_display_name: string | null;
  message_type: string;
  raw_text: string | null;
  sent_at: string;
};

type Classification = {
  message_id: string;
  business_category: string | null;
  priority: string | null;
  summary: string | null;
  action_required: boolean;
  confidence: number | null;
  requires_human_review: boolean;
};

type Media = {
  id: string;
  message_id: string;
  media_type: string;
  storage_path: string | null;
  mime_type: string | null;
};

type ImageAnalysis = {
  media_id: string;
  visual_description: string | null;
  ocr_text: string | null;
  detected_objects: string[] | null;
  image_type: string | null;
  possible_anomaly: boolean | null;
  anomaly_description: string | null;
  confidence: number | null;
};

type DocumentAnalysis = {
  media_id: string;
  document_type: string | null;
  summary: string | null;
  reference: string | null;
  client_name: string | null;
  site_name: string | null;
  waste_type: string | null;
  quantity: string | null;
  amount: string | null;
  full_text: string | null;
  possible_anomaly: boolean | null;
  anomaly_description: string | null;
  confidence: number | null;
};

type AudioTranscription = {
  media_id: string;
  transcript: string | null;
  language: string | null;
};

type EnrichedRow = Message & {
  classification: Classification | null;
  media: Media | null;
  vision: ImageAnalysis | null;
  document: DocumentAnalysis | null;
  audio: AudioTranscription | null;
};

const PRIORITY_COLORS: Record<string, string> = {
  urgent: "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  high: "bg-orange-100 text-orange-700 dark:bg-orange-950/40 dark:text-orange-300",
  medium: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950/40 dark:text-yellow-300",
  low: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400",
};

export default function MessagesPage() {
  const router = useRouter();
  const [rows, setRows] = useState<EnrichedRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterCategory, setFilterCategory] = useState<string>("all");
  const [filterPriority, setFilterPriority] = useState<string>("all");

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) router.replace("/login");
    });
  }, [router]);

  const fetchRows = useCallback(async () => {
    setLoading(true);

    // 1. Messages (les 100 plus récents)
    const { data: messages, error: msgErr } = await supabase
      .from("whatsapp_messages")
      .select(
        "id,external_message_id,sender_phone,sender_display_name,message_type,raw_text,sent_at"
      )
      .order("sent_at", { ascending: false })
      .limit(100);

    if (msgErr || !messages) {
      console.error("Fetch messages error:", msgErr);
      setRows([]);
      setLoading(false);
      return;
    }

    const ids = messages.map((m) => m.id);
    if (ids.length === 0) {
      setRows([]);
      setLoading(false);
      return;
    }

    // 2. Classifications de ces messages
    const { data: classifications } = await supabase
      .from("message_classifications")
      .select(
        "message_id,business_category,priority,summary,action_required,confidence,requires_human_review"
      )
      .in("message_id", ids);

    // 3. Médias de ces messages
    const { data: medias } = await supabase
      .from("whatsapp_media")
      .select("id,message_id,media_type,storage_path,mime_type")
      .in("message_id", ids);

    // 4. Analyses de contenu pour ces médias (photo / document / vocal)
    const mediaIds = (medias || []).map((m) => m.id);
    let visions: ImageAnalysis[] = [];
    let documents: DocumentAnalysis[] = [];
    let audios: AudioTranscription[] = [];
    if (mediaIds.length > 0) {
      const [visionsRes, docsRes, audiosRes] = await Promise.all([
        supabase
          .from("image_analysis")
          .select(
            "media_id,visual_description,ocr_text,detected_objects,image_type,possible_anomaly,anomaly_description,confidence"
          )
          .in("media_id", mediaIds),
        supabase
          .from("document_analysis")
          .select(
            "media_id,document_type,summary,reference,client_name,site_name,waste_type,quantity,amount,full_text,possible_anomaly,anomaly_description,confidence"
          )
          .in("media_id", mediaIds),
        supabase
          .from("audio_transcription")
          .select("media_id,transcript,language")
          .in("media_id", mediaIds),
      ]);
      visions = (visionsRes.data || []) as ImageAnalysis[];
      documents = (docsRes.data || []) as DocumentAnalysis[];
      audios = (audiosRes.data || []) as AudioTranscription[];
    }

    // 5. Merge
    const cMap = new Map<string, Classification>();
    (classifications || []).forEach((c) => cMap.set(c.message_id, c as Classification));

    const mMap = new Map<string, Media>();
    (medias || []).forEach((m) => mMap.set(m.message_id, m as Media));

    const vMap = new Map<string, ImageAnalysis>();
    visions.forEach((v) => vMap.set(v.media_id, v));

    const dMap = new Map<string, DocumentAnalysis>();
    documents.forEach((d) => dMap.set(d.media_id, d));

    const aMap = new Map<string, AudioTranscription>();
    audios.forEach((a) => aMap.set(a.media_id, a));

    const enriched: EnrichedRow[] = (messages as Message[]).map((m) => {
      const media = mMap.get(m.id) ?? null;
      return {
        ...m,
        classification: cMap.get(m.id) ?? null,
        media,
        vision: media ? (vMap.get(media.id) ?? null) : null,
        document: media ? (dMap.get(media.id) ?? null) : null,
        audio: media ? (aMap.get(media.id) ?? null) : null,
      };
    });

    setRows(enriched);
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
    if (filterCategory !== "all" && r.classification?.business_category !== filterCategory) return false;
    if (filterPriority !== "all" && r.classification?.priority !== filterPriority) return false;
    return true;
  });

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black">
      <Nav />
      <div className="border-b border-zinc-200 bg-white/80 backdrop-blur dark:border-zinc-800 dark:bg-black/80">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-2">
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Groupe ADS Multi Sites · {filtered.length} message
            {filtered.length > 1 ? "s" : ""}
          </p>
          <button
            onClick={fetchRows}
            className="rounded-md border border-zinc-300 px-3 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
          >
            Rafraîchir
          </button>
        </div>

        <div className="mx-auto flex max-w-7xl flex-wrap gap-2 border-t border-zinc-200 px-4 py-2 dark:border-zinc-800">
          <select
            value={filterCategory}
            onChange={(e) => setFilterCategory(e.target.value)}
            className="rounded-md border border-zinc-300 bg-white px-2 py-1 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
          >
            <option value="all">Toutes catégories</option>
            <option value="incident">Incident</option>
            <option value="urgence">Urgence</option>
            <option value="demande_action">Demande d&apos;action</option>
            <option value="validation">Validation</option>
            <option value="refus">Refus</option>
            <option value="livraison">Livraison</option>
            <option value="intervention">Intervention</option>
            <option value="panne">Panne</option>
            <option value="retard">Retard</option>
            <option value="document_recu">Document reçu</option>
            <option value="document_manquant">Document manquant</option>
            <option value="preuve_photo">Preuve photo</option>
            <option value="instruction">Instruction</option>
            <option value="cloture_action">Clôture action</option>
            <option value="info">Info</option>
            <option value="non_exploitable">Non exploitable</option>
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
      </div>

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

function MessageCard({ row }: { row: EnrichedRow }) {
  const c = row.classification;
  const media = row.media;
  const vision = row.vision;
  const doc = row.document;
  const audio = row.audio;
  const priorityClass = c?.priority
    ? PRIORITY_COLORS[c.priority] ?? PRIORITY_COLORS.low
    : "";

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

      {/* Analyse vision Claude */}
      {vision && (
        <div
          className={`mt-3 rounded-md border p-3 text-xs ${
            vision.possible_anomaly
              ? "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/20"
              : "border-zinc-100 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950"
          }`}
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
              👁 Analyse visuelle
            </span>
            {vision.image_type && (
              <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">
                {vision.image_type}
              </span>
            )}
            {vision.possible_anomaly && (
              <span className="rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                ⚠ ANOMALIE
              </span>
            )}
            {vision.confidence !== null && vision.confidence !== undefined && (
              <span className="text-[10px] text-zinc-500">
                conf {Math.round((vision.confidence || 0) * 100)}%
              </span>
            )}
          </div>
          {vision.visual_description && (
            <p className="mt-1.5 text-zinc-800 dark:text-zinc-200">
              {vision.visual_description}
            </p>
          )}
          {vision.anomaly_description && (
            <p className="mt-1 text-red-700 dark:text-red-400">
              ⚠ {vision.anomaly_description}
            </p>
          )}
          {vision.ocr_text && (
            <div className="mt-1.5 border-t border-zinc-200 pt-1.5 dark:border-zinc-800">
              <span className="text-[10px] font-medium uppercase text-zinc-500">
                Texte lu :
              </span>{" "}
              <span className="text-zinc-700 dark:text-zinc-300">
                {vision.ocr_text}
              </span>
            </div>
          )}
          {vision.detected_objects && vision.detected_objects.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {vision.detected_objects.map((obj, i) => (
                <span
                  key={i}
                  className="rounded bg-zinc-200 px-1 py-0.5 text-[10px] text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
                >
                  {obj}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Analyse de document (PDF / Excel / Word) */}
      {doc && (
        <div
          className={`mt-3 rounded-md border p-3 text-xs ${
            doc.possible_anomaly
              ? "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/20"
              : "border-zinc-100 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950"
          }`}
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
              📄 Document lu
            </span>
            {doc.document_type && (
              <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-[10px] text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300">
                {doc.document_type}
              </span>
            )}
            {doc.possible_anomaly && (
              <span className="rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                ⚠ ANOMALIE
              </span>
            )}
            {doc.confidence !== null && doc.confidence !== undefined && (
              <span className="text-[10px] text-zinc-500">
                conf {Math.round((doc.confidence || 0) * 100)}%
              </span>
            )}
          </div>
          {doc.summary && (
            <p className="mt-1.5 text-zinc-800 dark:text-zinc-200">{doc.summary}</p>
          )}
          {doc.anomaly_description && (
            <p className="mt-1 text-red-700 dark:text-red-400">⚠ {doc.anomaly_description}</p>
          )}
          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-zinc-600 dark:text-zinc-400">
            {doc.reference && <span>Réf : <strong>{doc.reference}</strong></span>}
            {doc.client_name && <span>Client : <strong>{doc.client_name}</strong></span>}
            {doc.site_name && <span>Site : <strong>{doc.site_name}</strong></span>}
            {doc.waste_type && <span>Matière : <strong>{doc.waste_type}</strong></span>}
            {doc.quantity && <span>Qté : <strong>{doc.quantity}</strong></span>}
            {doc.amount && <span>Montant : <strong>{doc.amount}</strong></span>}
          </div>
        </div>
      )}

      {/* Transcription de note vocale */}
      {audio && audio.transcript && (
        <div className="mt-3 rounded-md border border-zinc-100 bg-zinc-50 p-3 text-xs dark:border-zinc-800 dark:bg-zinc-950">
          <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
            🎙 Note vocale — transcription
          </span>
          <p className="mt-1.5 whitespace-pre-wrap text-zinc-800 dark:text-zinc-200">
            {audio.transcript}
          </p>
        </div>
      )}
    </li>
  );
}
