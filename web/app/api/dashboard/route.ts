/**
 * Proxy /api/dashboard → backend /api/dashboard
 * Permet au frontend d'appeler une URL same-origin et laisse Vercel (serveur)
 * faire le call HTTPS au backend. Évite les soucis de cert/firewall côté client.
 */

import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://opslens-api.duckdns.org";

export async function GET() {
  const targetUrl = `${BACKEND_URL}/api/dashboard`;
  try {
    const r = await fetch(targetUrl, {
      cache: "no-store",
      signal: AbortSignal.timeout(25000),
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
      { status: 502 }
    );
  }
}
