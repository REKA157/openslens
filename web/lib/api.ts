/**
 * Helpers pour appeler le backend FastAPI OpsLens.
 */

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "http://izomje1iggly4e23t8xr6p7v.2.24.15.60.sslip.io";

export async function fetchDashboard(): Promise<DashboardData> {
  const r = await fetch(`${BACKEND_URL}/api/dashboard`, {
    method: "GET",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`Dashboard ${r.status}`);
  return r.json();
}

export async function fetchLatestDailyReport(): Promise<DailyReport | null> {
  const r = await fetch(`${BACKEND_URL}/api/reports/daily/latest`, {
    method: "GET",
    cache: "no-store",
  });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`Daily ${r.status}`);
  return r.json();
}

export async function generateDailyReport(force = false): Promise<DailyReport> {
  const r = await fetch(`${BACKEND_URL}/admin/generate-daily-report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
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

export type DashboardData = {
  generated_at: string;
  kpis: { today: DashboardKPIs; yesterday: DashboardKPIs };
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
