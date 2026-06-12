-- v6 : table mkgt_operations (données ERP CKDEV/MKGT)
--
-- Stocke les commandes/opérations importées depuis les exports CSV MKGT.
-- Import idempotent : (company_id, import_batch_id, row_hash) est unique.
-- batch_id = SHA-1 du fichier CSV → réimporter le même fichier = 0 doublons.
--
-- À exécuter dans Supabase → SQL Editor avant de déployer le backend v42.

BEGIN;

CREATE TABLE IF NOT EXISTS mkgt_operations (
  id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id      UUID          NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  external_ref    TEXT,                                  -- N° BL / référence commande MKGT
  operation_date  DATE,                                  -- date de l'opération
  client_name     TEXT,                                  -- nom du client / commanditaire
  site_name       TEXT,                                  -- chantier tel que dans MKGT
  site_id         UUID          REFERENCES sites(id),    -- FK vers site canonique OpsLens
  waste_type      TEXT,                                  -- type de déchet (ferraille, PVC…)
  container_type  TEXT,                                  -- type de benne
  quantity        NUMERIC(12,3),                         -- quantité (poids ou volume)
  unit            TEXT,                                  -- T, m³, etc.
  status          TEXT,                                  -- planifié / réalisé / facturé / annulé
  driver          TEXT,
  vehicle         TEXT,
  amount_ht       NUMERIC(12,2),                         -- montant HT
  raw_data        JSONB         NOT NULL DEFAULT '{}',   -- ligne CSV originale complète
  import_batch_id TEXT          NOT NULL,                -- hash SHA-1 du fichier CSV
  row_hash        TEXT          NOT NULL,                -- hash de la ligne (idempotence)
  created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Idempotence : même fichier + même ligne → upsert silencieux
CREATE UNIQUE INDEX IF NOT EXISTS mkgt_ops_row_uidx
  ON mkgt_operations (company_id, import_batch_id, row_hash);

CREATE INDEX IF NOT EXISTS mkgt_ops_date_idx
  ON mkgt_operations (company_id, operation_date);

CREATE INDEX IF NOT EXISTS mkgt_ops_site_id_idx
  ON mkgt_operations (site_id)
  WHERE site_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS mkgt_ops_status_idx
  ON mkgt_operations (company_id, status);

-- RLS (service_role bypass activé par défaut côté backend)
ALTER TABLE mkgt_operations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "mkgt_authenticated_read" ON mkgt_operations
  FOR SELECT TO authenticated
  USING (TRUE);

COMMIT;
