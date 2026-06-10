-- v5 : table sites canoniques
--
-- Les noms de sites mentionnés dans les messages WhatsApp ont de multiples
-- variantes ("Le Plessis", "ADS IDF LE PLESSIS BELLEVILLE", "Plessis-Belleville",
-- etc.). On les regroupe ici en entités canoniques avec un tableau d'aliases.
--
-- L'extraction des entités se fait toujours dans `message_classifications.entities.sites`
-- (jsonb). Cette table sert de référentiel humain-validé pour rapprocher
-- chaque variante à un site officiel ADS.
--
-- À exécuter dans Supabase → SQL Editor avant de déployer le backend v22.

BEGIN;

CREATE TABLE IF NOT EXISTS sites (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  canonical_name TEXT NOT NULL,
  aliases TEXT[] NOT NULL DEFAULT '{}',
  region TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (company_id, canonical_name)
);

-- Index GIN sur aliases pour les recherches "telle variante appartient à quel site"
CREATE INDEX IF NOT EXISTS idx_sites_aliases ON sites USING GIN (aliases);
CREATE INDEX IF NOT EXISTS idx_sites_company ON sites (company_id, is_active);

-- Trigger updated_at
CREATE OR REPLACE FUNCTION touch_sites_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sites_updated_at ON sites;
CREATE TRIGGER trg_sites_updated_at
  BEFORE UPDATE ON sites
  FOR EACH ROW
  EXECUTE FUNCTION touch_sites_updated_at();

-- RLS : ouverte côté backend (on utilise service_role)
ALTER TABLE sites ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "sites_authenticated_read" ON sites;
CREATE POLICY "sites_authenticated_read" ON sites
  FOR SELECT TO authenticated
  USING (TRUE);

COMMIT;
