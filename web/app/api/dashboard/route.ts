/**
 * Proxy /api/dashboard → backend /api/dashboard
 */

import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://opslens-api.duckdns.org";

function describeError(err: unknown): Record<string, unknown> {
  if (!(err instanceof Error)) {
    return { raw: String(err) };
  }
  const out: Record<string, unknown> = {
    name: err.name,
    message: err.message,
  };
  // Node fetch erreurs ont en général un .cause avec les vrais détails
  const cause = (err as Error & { cause?: unknown }).cause;
  if (cause && typeof cause === "object") {
    const c = cause as Record<string, unknown>;
    out.cause = {
      message: c.message,
      code: c.code,
      errno: c.errno,
      syscall: c.syscall,
      hostname: c.hostname,
      address: c.address,
      port: c.port,
    };
  }
  return out;
}

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
    return NextResponse.json(
      { error: "Proxy failed", target: targetUrl, ...describeError(err) },
      { status: 502 }
    );
  }
}
