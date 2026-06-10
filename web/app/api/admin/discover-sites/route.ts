/**
 * Proxy POST /api/admin/discover-sites → backend /admin/discover-sites
 * Appel Claude Sonnet → 30-45 sec selon volume.
 */

import { NextRequest, NextResponse } from "next/server";
import { Agent, fetch as undiciFetch } from "undici";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 90;

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://opslens-api.duckdns.org";

const insecureDispatcher = new Agent({
  connect: { rejectUnauthorized: false },
});

export async function POST(req: NextRequest) {
  const targetUrl = `${BACKEND_URL}/admin/discover-sites`;
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
