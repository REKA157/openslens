/**
 * Proxy GET /api/predictions/insights/status?job_id=... → backend
 * Poll rapide (<2s).
 */

import { NextRequest, NextResponse } from "next/server";
import { Agent, fetch as undiciFetch } from "undici";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://opslens-api.duckdns.org";

const insecureDispatcher = new Agent({
  connect: { rejectUnauthorized: false },
});

export async function GET(req: NextRequest) {
  const u = new URL(req.url);
  const jobId = u.searchParams.get("job_id");
  if (!jobId) {
    return NextResponse.json(
      { error: "job_id manquant" },
      { status: 400 },
    );
  }
  const targetUrl = `${BACKEND_URL}/api/predictions/insights/status?job_id=${encodeURIComponent(jobId)}`;
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
