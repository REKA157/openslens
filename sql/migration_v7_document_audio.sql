-- v7 : analyse des documents (PDF/Office) et transcription des notes vocales
--
-- Jusqu'ici seules les IMAGES étaient analysées (table image_analysis). Les
-- documents (bons de pesée, BSD, factures, fiches de déclassement) étaient
-- téléchargés mais jamais lus, et les notes vocales jamais transcrites.
--
-- Ces deux tables stockent l'extraction, qui est ensuite fusionnée dans la
-- classification du message (comme pour les photos).
--
-- À exécuter dans Supabase → SQL Editor avant de déployer le backend v46.

BEGIN;

-- ---- Analyse de documents (PDF lu par Claude, Excel/Word extraits) ----------
CREATE TABLE IF NOT EXISTS document_analysis (
  id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  media_id        UUID         NOT NULL REFERENCES whatsapp_media(id) ON DELETE CASCADE,
  document_type   TEXT,                       -- bon_pesee | bon_livraison | fiche_declassement | facture | bsd | bon_commande | autre
  summary         TEXT,                       -- 1-2 phrases factuelles
  reference       TEXT,                       -- n° BL / réf / n° facture
  client_name     TEXT,
  site_name       TEXT,
  waste_type      TEXT,
  quantity        TEXT,                       -- texte libre (ex "12,5 T")
  amount          TEXT,
  doc_dates       TEXT[]       NOT NULL DEFAULT '{}',
  full_text       TEXT,                       -- texte intégral extrait (tronqué)
  possible_anomaly BOOLEAN     NOT NULL DEFAULT FALSE,
  anomaly_description TEXT,
  confidence      NUMERIC(3,2),
  model_used      TEXT,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (media_id)
);

CREATE INDEX IF NOT EXISTS idx_document_analysis_media ON document_analysis (media_id);
CREATE INDEX IF NOT EXISTS idx_document_analysis_type ON document_analysis (document_type);

-- ---- Transcription des notes vocales (Whisper) ------------------------------
CREATE TABLE IF NOT EXISTS audio_transcription (
  id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
  media_id     UUID         NOT NULL REFERENCES whatsapp_media(id) ON DELETE CASCADE,
  transcript   TEXT,
  language     TEXT,
  duration_seconds NUMERIC,
  model_used   TEXT,
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  UNIQUE (media_id)
);

CREATE INDEX IF NOT EXISTS idx_audio_transcription_media ON audio_transcription (media_id);

-- ---- RLS (lecture côté front authentifié, écriture service_role) ------------
ALTER TABLE document_analysis ENABLE ROW LEVEL SECURITY;
ALTER TABLE audio_transcription ENABLE ROW LEVEL SECURITY;

CREATE POLICY "document_analysis_read" ON document_analysis
  FOR SELECT TO authenticated USING (TRUE);
CREATE POLICY "audio_transcription_read" ON audio_transcription
  FOR SELECT TO authenticated USING (TRUE);

COMMIT;
