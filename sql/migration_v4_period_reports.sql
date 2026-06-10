-- v4 : rapports hebdomadaires et mensuels
--
-- On garde la table existante `daily_reports` et on étend :
--   - `period_type`  : 'day' | 'week' | 'month'
--   - `period_end`   : DATE de fin de période (pour day : = report_date)
--
-- L'unicité passe de (company_id, report_date) à
-- (company_id, period_type, report_date) — on peut donc avoir
-- pour le 2026-06-08 : un rapport jour, un rapport semaine (lundi 8 juin),
-- et un rapport mois (juin si 1er).
--
-- À exécuter dans Supabase → SQL Editor AVANT de déployer le backend v17.

BEGIN;

ALTER TABLE daily_reports
  ADD COLUMN IF NOT EXISTS period_type TEXT NOT NULL DEFAULT 'day'
    CHECK (period_type IN ('day', 'week', 'month')),
  ADD COLUMN IF NOT EXISTS period_end DATE;

-- Pour les rapports existants (quotidiens), period_end = report_date
UPDATE daily_reports SET period_end = report_date WHERE period_end IS NULL;

ALTER TABLE daily_reports ALTER COLUMN period_end SET NOT NULL;

-- Remplacer la contrainte d'unicité
ALTER TABLE daily_reports
  DROP CONSTRAINT IF EXISTS daily_reports_company_id_report_date_key;

ALTER TABLE daily_reports
  ADD CONSTRAINT daily_reports_unique_period
    UNIQUE (company_id, period_type, report_date);

CREATE INDEX IF NOT EXISTS idx_daily_reports_period_type
  ON daily_reports (period_type, report_date DESC);

COMMIT;
