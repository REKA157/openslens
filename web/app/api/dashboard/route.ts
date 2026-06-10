/**
 * Proxy /api/dashboard → backend /api/dashboard
 *
 * On utilise un dispatcher undici qui ignore la validation du cert TLS,
 * car notre Caddy en cours d'install peut servir un cert self-signed.
 * C'est OK : on parle à NOTRE backend, pas un tiers.
 */

import { NextResponse } from "next/server";
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

function describeError(err: unknown): Record<string, unknown> {
  if (!(err instanceof Error)) return { raw: String(err) };
  const out: Record<string, unknown> = { name: err.name, message: err.message };
  const cause = (err as Error & { cause?: unknown }).cause;
  if (cause && typeof cause === "object") {
    const c = cause as Record<string, unknown>;
    out.cause = {
      message: c.message,
      code: c.code,
      syscall: c.syscall,
      hostname: c.hostname,
      address: c.address,
      port: c.port,
    };
  }
  return out;
}

export async function GET(req: Request) {
  const incoming = new URL(req.url);
  const qs = new URLSearchParams();
  const d = incoming.searchParams.get("date");
  const p = incoming.searchParams.get("period");
  if (d) qs.set("date", d);
  if (p) qs.set("period", p);
  const targetUrl =
    `${BACKEND_URL}/api/dashboard` + (qs.toString() ? `?${qs.toString()}` : "");
  try {
    const r = await undiciFetch(targetUrl, {
      dispatcher: insecureDispatcher,
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
