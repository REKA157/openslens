-- ============================================================
-- Migration v2 — tables d'analyse IA
-- À exécuter UNE FOIS dans Supabase SQL Editor après le schéma v1.
-- ============================================================

-- 1. Classification métier des messages
create table if not exists message_classifications (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references whatsapp_messages(id) on delete cascade,
  business_category text,
  priority text,
  language text,
  summary text,
  entities jsonb,
  action_required boolean default false,
  action_description text,
  deadline timestamptz,
  risk_level text,
  operational_impact text,
  requires_human_review boolean default false,
  confidence float,
  human_corrected boolean default false,
  human_correction jsonb,
  model_used text,
  created_at timestamptz default now(),
  unique (message_id)
);
create index if not exists classif_priority_idx
  on message_classifications(priority, created_at desc);
create index if not exists classif_category_idx
  on message_classifications(business_category, created_at desc);

-- 2. Transcriptions audio
create table if not exists audio_transcriptions (
  id uuid primary key default gen_random_uuid(),
  media_id uuid not null references whatsapp_media(id) on delete cascade,
  language text,
  transcription text,
  summary text,
  word_segments jsonb,
  confidence float,
  model_used text,
  created_at timestamptz default now(),
  unique (media_id)
);

-- 3. Analyse visuelle des images
create table if not exists image_analysis (
  id uuid primary key default gen_random_uuid(),
  media_id uuid not null references whatsapp_media(id) on delete cascade,
  visual_description text,
  ocr_text text,
  detected_objects jsonb,
  image_type text,
  possible_anomaly boolean,
  anomaly_description text,
  confidence float,
  model_used text,
  created_at timestamptz default now(),
  unique (media_id)
);

-- 4. RLS sur ces nouvelles tables
alter table message_classifications enable row level security;
alter table audio_transcriptions enable row level security;
alter table image_analysis enable row level security;

create policy "read_classifications_auth" on message_classifications
  for select to authenticated using (true);
create policy "read_transcriptions_auth" on audio_transcriptions
  for select to authenticated using (true);
create policy "read_image_analysis_auth" on image_analysis
  for select to authenticated using (true);
