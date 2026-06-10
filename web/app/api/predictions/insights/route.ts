/**
 * Proxy POST /api/predictions/insights → backend
 * Appel Claude Sonnet : 15-30 sec.
 */

import { NextRequest, NextResponse } from "next/server";
import { Agent, fetch as undiciFetch } from "undici";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// Vercel Pro plan supporte jusqu'à 300s. Sonnet prend 15-30s, load corpus 5-8s.
export const maxDuration = 300;

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://opslens-api.duckdns.org";

const insecureDispatcher = new Agent({
  connect: { rejectUnauthorized: false },
});

export async function POST(req: NextRequest) {
  const u = new URL(req.url);
  const d = u.searchParams.get("date");
  const qs = d ? `?date=${encodeURIComponent(d)}` : "";
  const targetUrl = `${BACKEND_URL}/api/predictions/insights${qs}`;
  try {
    const r = await undiciFetch(targetUrl, {
      method: "POST",
      dispatcher: insecureDispatcher,
    });
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
