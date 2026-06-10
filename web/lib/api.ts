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
): Promise<DashboardData> {
  const qs = new URLSearchParams();
  if (date) qs.set("date", date);
  qs.set("period", period);
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
