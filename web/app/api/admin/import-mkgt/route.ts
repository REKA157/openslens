/**
 * Proxy POST /api/admin/import-mkgt → backend /admin/import-mkgt-csv
 *
 * Transmet le fichier multipart tel quel au backend FastAPI.
 * Le token admin est injecté côté serveur (ADMIN_TOKEN env var).
 */

import { NextRequest, NextResponse } from "next/server";
import { Agent, fetch as undiciFetch } from "undici";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://opslens-api.duckdns.org";

const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";

const insecureDispatcher = new Agent({
  connect: { rejectUnauthorized: false },
});

export async function POST(request: NextRequest) {
  const targetUrl = `${BACKEND_URL}/admin/import-mkgt-csv`;
  try {
    // On transmet les octets bruts du multipart tels quels, en conservant le
    // content-type d'origine (avec sa "boundary"). Plus simple et plus sûr que
    // de reconstruire un FormData — et compatible avec les types undici.
    const rawBody = Buffer.from(await request.arrayBuffer());
    const headers: Record<string, string> = {
      "content-type": request.headers.get("content-type") || "application/octet-stream",
    };
    if (ADMIN_TOKEN) {
      headers["X-Admin-Token"] = ADMIN_TOKEN;
    }

    const r = await undiciFetch(targetUrl, {
      method: "POST",
      headers,
      body: rawBody,
      dispatcher: insecureDispatcher,
    });

    const responseBody = await r.text();
    return new NextResponse(responseBody, {
      status: r.status,
      headers: { "content-type": "application/json" },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { error: "Proxy failed", target: targetUrl, message: msg },
      { status: 502 }
    );
  }
}
