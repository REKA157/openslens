/**
 * Proxy GET /api/forecast → backend
 * Premier appel : 30-90 sec (Prophet entraîne 1 modèle/site).
 * Appels suivants : <2 sec (cache mémoire 1h backend).
 */

import { NextRequest, NextResponse } from "next/server";
import { Agent, fetch as undiciFetch } from "undici";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 300;

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
  const h = u.searchParams.get("horizon_days");
  const s = u.searchParams.get("site_id");
  if (h) qs.set("horizon_days", h);
  if (s) qs.set("site_id", s);
  const targetUrl =
    `${BACKEND_URL}/api/forecast` + (qs.toString() ? `?${qs.toString()}` : "");
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
