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

export async function fetchLatestDailyReport(): Promise<DailyReport | null> {
  const r = await fetch(`/api/reports/daily/latest`, {
    method: "GET",
    cache: "no-store",
  });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`Daily ${r.status}`);
  return r.json();
}

export async function fetchDailyReportByDate(
  date: string,
): Promise<DailyReport | null> {
  const r = await fetch(`/api/reports/daily/${date}`, {
    method: "GET",
    cache: "no-store",
  });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`Daily ${date} ${r.status}`);
  return r.json();
}

export type DailyReportSummary = {
  report_date: string;
  created_at: string;
};

export async function fetchDailyReportsList(): Promise<DailyReportSummary[]> {
  const r = await fetch(`/api/reports/daily`, {
    method: "GET",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`Daily list ${r.status}`);
  const j = (await r.json()) as { reports: DailyReportSummary[] };
  return j.reports || [];
}

export async function generateDailyReport(
  force = false,
  targetDate?: string,
): Promise<DailyReport> {
  const body: Record<string, unknown> = { force };
  if (targetDate) body.target_date = targetDate;
  const r = await fetch(`/api/admin/generate-daily-report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.text();
    throw new Error(`Generate ${r.status}: ${err}`);
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
  content: DailyReportContent;
  stats: {
    total_messages: number;
    by_priority: Record<string, number>;
    by_category: Record<string, number>;
    action_required_count: number;
    sites_mentioned: string[];
  };
  model_used: string;
  created_at: string;
};
