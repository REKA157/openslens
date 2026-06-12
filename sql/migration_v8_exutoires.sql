-- v8 : suivi des apports de déchets ultimes par EXUTOIRE
--
-- ADS IDF NORD est engagée contractuellement à livrer un tonnage minimum de
-- déchets ultimes sur des exutoires (SEMARDEL, SUEZ Liancourt, SUEZ Capoulade
-- Prudemanche, EMTA). On suit ici contractuel vs réel par mois.
--
-- Deux tables :
--   exutoires             : référentiel + objectifs contractuels annuels
--   exutoire_monthly_real : tonnage réel mensuel (saisie/seed) — utilisé en
--                           repli si MKGT n'est pas encore alimenté.
--
-- Le réel est calculé en priorité depuis mkgt_operations (match des aliases sur
-- client/site), sinon depuis exutoire_monthly_real.
--
-- À exécuter dans Supabase → SQL Editor avant de déployer le backend v48.

BEGIN;

CREATE TABLE IF NOT EXISTS exutoires (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id               UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  canonical_name           TEXT NOT NULL,
  parent_group             TEXT,                 -- ex 'SUEZ' pour regrouper les sous-centres
  aliases                  TEXT[] NOT NULL DEFAULT '{}',
  contractual_annual_min   NUMERIC(12,2) NOT NULL DEFAULT 0,
  contractual_annual_max   NUMERIC(12,2),
  waste_filter             TEXT,                 -- optionnel : ne compter que ce type (ex 'ultime')
  is_active                BOOLEAN NOT NULL DEFAULT TRUE,
  notes                    TEXT,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (company_id, canonical_name)
);

CREATE INDEX IF NOT EXISTS idx_exutoires_company ON exutoires (company_id, is_active);

CREATE TABLE IF NOT EXISTS exutoire_monthly_real (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id    UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  exutoire_id   UUID NOT NULL REFERENCES exutoires(id) ON DELETE CASCADE,
  year          INT NOT NULL,
  month         INT NOT NULL CHECK (month BETWEEN 1 AND 12),
  tonnage_real  NUMERIC(12,2) NOT NULL DEFAULT 0,
  source        TEXT NOT NULL DEFAULT 'manual',   -- manual | mkgt
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (company_id, exutoire_id, year, month)
);

CREATE INDEX IF NOT EXISTS idx_exutoire_real ON exutoire_monthly_real (company_id, exutoire_id, year);

CREATE OR REPLACE FUNCTION touch_exutoires_updated_at()
RETURNS TRIGGER AS $$ BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_exutoires_updated_at ON exutoires;
CREATE TRIGGER trg_exutoires_updated_at BEFORE UPDATE ON exutoires
  FOR EACH ROW EXECUTE FUNCTION touch_exutoires_updated_at();

ALTER TABLE exutoires ENABLE ROW LEVEL SECURITY;
ALTER TABLE exutoire_monthly_real ENABLE ROW LEVEL SECURITY;
CREATE POLICY "exutoires_read" ON exutoires FOR SELECT TO authenticated USING (TRUE);
CREATE POLICY "exutoire_real_read" ON exutoire_monthly_real FOR SELECT TO authenticated USING (TRUE);

COMMIT;
