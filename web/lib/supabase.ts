/**
 * Client Supabase pour le navigateur.
 * On utilise une seule instance partagée pour toute l'app.
 */

import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!;

if (!url || !key) {
  throw new Error(
    "Variables d'environnement Supabase manquantes — vérifie .env.local"
  );
}

export const supabase = createClient(url, key, {
  auth: {
    persistSession: true,
    autoRefreshToken: true,
  },
});

// Types partagés
export type Message = {
  id: string;
  external_message_id: string;
  sender_phone: string | null;
  sender_display_name: string | null;
  message_type: string;
  raw_text: string | null;
  sent_at: string;
  ingested_at: string;
};

export type Classification = {
  id: string;
  message_id: string;
  business_category: string | null;
  priority: string | null;
  summary: string | null;
  action_required: boolean;
  action_description: string | null;
  confidence: number | null;
  requires_human_review: boolean;
};

export type Media = {
  id: string;
  message_id: string;
  media_type: string;
  storage_path: string | null;
  mime_type: string | null;
  status: string;
};
