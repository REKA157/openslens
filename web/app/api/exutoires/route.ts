/** Proxy /api/exutoires → backend /api/exutoires (bypass cert self-signed). */
import { NextResponse } from "next/server";
import { Agent, fetch as undiciFetch } from "undici";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://opslens-api.duckdns.org";

const insecureDispatcher = new Agent({ connect: { rejectUnauthorized: false } });

export async function GET(req: Request) {
  const incoming = new URL(req.url);
  const qs = new URLSearchParams();
  const year = incoming.searchParams.get("year");
  if (year) qs.set("year", year);
  const targetUrl = `${BACKEND_URL}/api/exutoires${qs.toString() ? `?${qs}` : ""}`;
  try {
    const r = await undiciFetch(targetUrl, { dispatcher: insecureDispatcher });
    const body = await r.text();
    return new NextResponse(body, {
      status: r.status,
      headers: { "content-type": r.headers.get("content-type") || "application/json" },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: "Proxy failed", target: targetUrl, message: msg }, { status: 502 });
  }
}
