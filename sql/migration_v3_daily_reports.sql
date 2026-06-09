-- ============================================================
-- Migration v3 — table des rapports quotidiens
-- À exécuter UNE FOIS dans Supabase SQL Editor.
-- ============================================================

create table if not exists daily_reports (
  id uuid primary key default gen_random_uuid(),
  company_id uuid not null references companies(id) on delete cascade,
  report_date date not null,
  content jsonb not null,
  model_used text,
  stats jsonb,
  created_at timestamptz default now(),
  unique (company_id, report_date)
);

create index if not exists daily_reports_date_idx
  on daily_reports(company_id, report_date desc);

alter table daily_reports enable row level security;

create policy "read_daily_reports_auth" on daily_reports
  for select to authenticated using (true);
