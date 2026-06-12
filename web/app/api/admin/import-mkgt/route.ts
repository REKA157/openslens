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
    const formData = await request.formData();
    const headers: Record<string, string> = {};
    if (ADMIN_TOKEN) {
      headers["X-Admin-Token"] = ADMIN_TOKEN;
    }

    // Reconstruit le FormData pour undici
    const body = new FormData();
    for (const [key, value] of formData.entries()) {
      body.append(key, value);
    }

    const r = await undiciFetch(targetUrl, {
      method: "POST",
      headers,
      body,
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
