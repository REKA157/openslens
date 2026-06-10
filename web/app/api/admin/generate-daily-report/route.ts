/**
 * Proxy POST /api/admin/generate-daily-report → backend /admin/generate-daily-report
 *
 * (Note : côté frontend on appelle /api/admin/... mais le backend expose à
 *  /admin/... sans /api. Le proxy fait l'adaptation.)
 */

import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// La génération peut prendre 15-20 sec (appel Claude Sonnet)
export const maxDuration = 60;

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://opslens-api.duckdns.org";

export async function POST(request: NextRequest) {
  const targetUrl = `${BACKEND_URL}/admin/generate-daily-report`;
  try {
    const body = await request.text();
    const r = await fetch(targetUrl, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(55000),
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
      { status: 502 }
    );
  }
}
