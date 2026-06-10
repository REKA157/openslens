/**
 * Helpers pour appeler le backend FastAPI OpsLens.
 *
 * On passe par les routes proxy de Next.js (/api/...) qui forwardent
 * vers le backend réel. Avantages :
 *  - same-origin pour le navigateur (pas de CORS, pas de souci de cert)
 *  - Vercel serveur fait l'appel HTTPS au backend sans Bitdefender/firewall
 *  - même comportement en local dev et en prod Vercel
 */

export type DashboardPeriod = "day" | "week" | "month";

export async function fetchDashboard(
  date?: string,
  period: DashboardPeriod = "day",
  siteId?: string,
): Promise<DashboardData> {
  const qs = new URLSearchParams();
  if (date) qs.set("date", date);
  qs.set("period", period);
  if (siteId) qs.set("site_id", siteId);
  const r = await fetch(`/api/dashboard?${qs.toString()}`, {
    method: "GET",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`Dashboard ${r.status}`);
  return r.json();
}

export type ReportPeriod = "day" | "week" | "month";

export async function fetchReport(
  period: ReportPeriod,
  date: string,
): Promise<DailyReport | null> {
  const qs = new URLSearchParams({ period, date });
  const r = await fetch(`/api/reports?${qs.toString()}`, {
    method: "GET",
    cache: "no-store",
  });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`Report ${period}/${date} ${r.status}`);
  return r.json();
}

export type ReportSummary = {
  report_date: string;
  period_end?: string;
  created_at: string;
  stats?: { total_messages?: number } | null;
};

export async function fetchReportsList(
  period: ReportPeriod,
): Promise<ReportSummary[]> {
  const r = await fetch(`/api/reports/list?period=${period}`, {
    method: "GET",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`Reports list ${period} ${r.status}`);
  const j = (await r.json()) as { reports: ReportSummary[] };
  return j.reports || [];
}

export async function generateReport(
  period: ReportPeriod,
  targetDate: string,
  force = false,
): Promise<DailyReport> {
  const r = await fetch(`/api/reports`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ period, target_date: targetDate, force }),
  });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(`Generate ${period} ${r.status}: ${err}`);
  }
  return r.json();
}

// --- Types ---

export type DashboardKPIs = {
  messages: number;
  incidents: number;
  urgent: number;
  high: number;
  demande_action: number;
  action_required: number;
  livraisons: number;
};

export type UrgentItem = {
  message_id: string;
  sender: string | null;
  sent_at: string;
  category: string | null;
  priority: string | null;
  summary: string | null;
  action_required: boolean | null;
  raw_text: string;
};

export type DashboardWindow = { start: string; end: string };

export type DashboardData = {
  generated_at: string;
  period: DashboardPeriod;
  label: string;
  current_window: DashboardWindow;
  previous_window: DashboardWindow;
  kpis: { current: DashboardKPIs; previous: DashboardKPIs };
  categories: { category: string; count: number }[];
  priorities: { priority: string; count: number }[];
  top_sites: { site: string; count: number }[];
  top_senders: { sender: string; count: number }[];
  urgent_items: UrgentItem[];
};

export type DailyReportContent = {
  headline: string;
  narrative: string;
  urgent_points: string[];
  site_activity: Record<string, string>;
  open_actions: string[];
  recommendations: string[];
};

export type DailyReport = {
  id: string;
  report_date: string;
  period_type?: ReportPeriod;
  period_end?: string;
  content: DailyReportContent;
  stats: {
    period_type?: ReportPeriod;
    period_start?: string;
    period_end?: string;
    total_messages: number;
    by_priority: Record<string, number>;
    by_category: Record<string, number>;
    action_required_count: number;
    sites_mentioned: string[];
  };
  model_used: string;
  created_at: string;
};

// --- Sites ---

export type Site = {
  id?: string;
  canonical_name: string;
  aliases: string[];
  region: string | null;
  notes: string | null;
  is_active: boolean;
  message_count?: number;
};

export async function fetchSites(): Promise<Site[]> {
  const r = await fetch(`/api/sites`, { method: "GET", cache: "no-store" });
  if (!r.ok) throw new Error(`Sites ${r.status}`);
  const j = (await r.json()) as { sites: Site[] };
  return j.sites || [];
}

export async function fetchSite(siteId: string): Promise<Site> {
  const r = await fetch(`/api/sites/${siteId}`, {
    method: "GET",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`Site ${siteId} ${r.status}`);
  return r.json();
}

// --- Predictions ---

export type SiteAnomaly = {
  site_id: string;
  site_name: string;
  region: string | null;
  current_week_start: string;
  volume: { current: number; mean_history: number; z_score: number | null };
  urgent: { current: number; mean_history: number; z_score: number | null };
  severity: "medium" | "high";
  history_weeks_used: number;
};

export type SiteTrend = {
  site_id: string;
  site_name: string;
  region: string | null;
  window_recent: [string, string];
  window_prev: [string, string];
  volume: { recent: number; prev: number; delta_pct: number };
  by_category: Record<
    string,
    { recent: number; prev: number; delta_pct: number }
  >;
};

export type SiteForecast = {
  site_id: string;
  site_name: string;
  region: string | null;
  next_week_start: string;
  expected_total: number;
  confidence_band: number;
  by_day: { day: string; expected: number; stdev: number }[];
  history_weeks: number;
};

export type RecurringFailure = {
  site_id: string;
  site_name: string;
  vehicle: string;
  incidents_count: number;
  examples: { date: string; summary: string; priority: string | null }[];
};

export type PredictionsData = {
  ref_date: string;
  sites_count: number;
  messages_scanned: number;
  classifications_loaded: number;
  anomalies: SiteAnomaly[];
  trends: SiteTrend[];
  forecast: SiteForecast[];
  recurring_failures: RecurringFailure[];
  warning?: string;
};

export async function fetchPredictions(
  date?: string,
): Promise<PredictionsData> {
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  const r = await fetch(`/api/predictions${qs}`, {
    method: "GET",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`Predictions ${r.status}`);
  return r.json();
}

// --- AI Insights (Claude Sonnet) ---

export type AlertSeverity = "critical" | "warning" | "info";
export type AlertCategory =
  | "surcharge"
  | "qualite_securite"
  | "equipement"
  | "silence_anormal"
  | "opportunite";

export type AiAlert = {
  site_id: string | null;
  site_name: string;
  severity: AlertSeverity;
  category: AlertCategory;
  title: string;
  evidence: string;
  recommended_actions: string[];
  timeline: "immediat" | "cette_semaine" | "ce_mois";
};

export type CrossSignal = {
  title: string;
  involved_sites: string[];
  explanation: string;
  implications: string;
};

export type AiInsights = {
  narrative_overview: string;
  alerts: AiAlert[];
  cross_signals: CrossSignal[];
  recommendations_by_site: Record<string, string[]>;
};

export type PredictionsInsightsData = PredictionsData & {
  insights: AiInsights;
};

export async function generatePredictionsInsights(
  date?: string,
): Promise<PredictionsInsightsData> {
  const qs = date ? `?date=${encodeURIComponent(date)}` : "";
  const r = await fetch(`/api/predictions/insights${qs}`, {
    method: "POST",
    cache: "no-store",
  });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(`Insights ${r.status}: ${err}`);
  }
  return r.json();
}

export type DiscoverProposal = {
  sites: {
    canonical_name: string;
    region: string | null;
    aliases: string[];
    total_occurrences: number;
    notes?: string | null;
  }[];
  noise: { name: string; reason: string }[];
  uncertain: { name: string; reason: string }[];
};

export type DiscoverResponse = {
  classifications_scanned: number;
  raw_distinct: number;
  after_filter: number;
  proposal: DiscoverProposal;
};

export async function discoverSites(
  minOccurrences = 2,
): Promise<DiscoverResponse> {
  const r = await fetch(`/api/admin/discover-sites`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ min_occurrences: minOccurrences }),
  });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(`Discover ${r.status}: ${err}`);
  }
  return r.json();
}

export async function saveSites(
  sites: Site[],
  replaceAll = true,
): Promise<{ received: number; deleted_before: number; upserted: number }> {
  const r = await fetch(`/api/admin/save-sites`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sites: sites.map((s) => ({
        canonical_name: s.canonical_name,
        aliases: s.aliases,
        region: s.region,
        notes: s.notes,
        is_active: s.is_active,
      })),
      replace_all: replaceAll,
    }),
  });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(`Save sites ${r.status}: ${err}`);
  }
  return r.json();
}
