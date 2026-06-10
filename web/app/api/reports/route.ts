/**
 * Proxy GET /api/reports?period=&date= → backend
 * Et POST /api/reports → backend /admin/generate-report (génération unifiée).
 */

import { NextRequest, NextResponse } from "next/server";
import { Agent, fetch as undiciFetch } from "undici";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// La génération mensuelle peut prendre 30–45 s
export const maxDuration = 90;

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://opslens-api.duckdns.org";

const insecureDispatcher = new Agent({
  connect: { rejectUnauthorized: false },
});

export async function GET(req: NextRequest) {
  const u = new URL(req.url);
  const qs = new URLSearchParams();
  const period = u.searchParams.get("period");
  const date = u.searchParams.get("date");
  if (period) qs.set("period", period);
  if (date) qs.set("date", date);
  const targetUrl =
    `${BACKEND_URL}/api/reports` + (qs.toString() ? `?${qs.toString()}` : "");
  try {
    const r = await undiciFetch(targetUrl, { dispatcher: insecureDispatcher });
    const body = await r.text();
    return new NextResponse(body, {
      status: r.status,
      headers: { "content-type": r.headers.get("content-type") || "application/json" },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { error: "Proxy failed", target: targetUrl, message: msg },
      { status: 502 },
    );
  }
}

export async function POST(req: NextRequest) {
  const targetUrl = `${BACKEND_URL}/admin/generate-report`;
  try {
    const body = await req.text();
    const r = await undiciFetch(targetUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      dispatcher: insecureDispatcher,
    });
    const responseBody = await r.text();
    return new NextResponse(responseBody, {
      status: r.status,
      headers: { "content-type": r.headers.get("content-type") || "application/json" },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { error: "Proxy failed", target: targetUrl, message: msg },
      { status: 502 },
    );
  }
}
