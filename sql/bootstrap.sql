-- ============================================================
-- Bootstrap OpsLens — à exécuter UNE FOIS dans Supabase SQL Editor
-- après le schéma v1.
-- ============================================================

-- 1. Rectifier le nom de la company (créée initialement comme "Minegrid"
--    mais le pilote opérationnel est ADS)
update companies
set name = 'ADS',
    country = 'FR',
    timezone = 'Europe/Paris'
where name in ('Minegrid', 'ADS');

-- 2. Récupérer l'UUID de la company à mettre dans le .env du backend
--    (variable COMPANY_ID)
select id as company_id, name from companies;
