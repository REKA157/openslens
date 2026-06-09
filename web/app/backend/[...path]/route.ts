/**
 * Proxy explicite vers le backend FastAPI OpsLens.
 *
 * Toutes les requêtes vers /backend/* sont relayées vers BACKEND_URL/*.
 * On gère ici le forwarding manuellement, ce qui permet :
 *  - de logger côté serveur ce qui se passe (utile pour debugger 503)
 *  - de gérer HTTPS avec cert self-signed si besoin (NODE_TLS_REJECT_UNAUTHORIZED)
 *  - de ne pas dépendre des rewrites Next.js qui sont opaques sur Vercel
 *
 * Cette route est invoquée à la fois en local (npm run dev) et sur Vercel
 * comme une serverless function.
 */

import { NextRequest, NextResponse } from "next/server";
import { Agent } from "undici";

const BACKEND_URL =
  process.env.BACKEND_URL ||
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "https://izomje1iggly4e23t8xr6p7v.2.24.15.60.sslip.io";

// Dispatcher qui ignore les certs self-signed (Caddy en fallback sur sslip.io)
const insecureDispatcher = new Agent({
  connect: { rejectUnauthorized: false },
});

async function proxy(request: NextRequest, segments: string[]) {
  const path = segments.join("/");
  const search = request.nextUrl.search;
  const targetUrl = `${BACKEND_URL}/${path}${search}`;

  // Reconstruire les headers utiles (on enlève host, x-forwarded-*, etc.)
  const fwdHeaders: Record<string, string> = {};
  for (const [k, v] of request.headers.entries()) {
    const kl = k.toLowerCase();
    if (
      kl.startsWith("x-") ||
      kl === "host" ||
      kl === "connection" ||
      kl === "content-length"
    )
      continue;
    fwdHeaders[k] = v;
  }

  let body: BodyInit | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    body = await request.text();
  }

  try {
    // @ts-expect-error - dispatcher est supporté par undici sous Node
    const upstream = await fetch(targetUrl, {
      method: request.method,
      headers: fwdHeaders,
      body,
      cache: "no-store",
      // 25s pour éviter les timeouts Vercel (limit ~30s sur Hobby)
      signal: AbortSignal.timeout(25000),
      dispatcher: insecureDispatcher,
    });

    // Streamer la réponse au client (text car JSON+autre)
    const responseBody = await upstream.text();
    const responseHeaders = new Headers();
    upstream.headers.forEach((v, k) => {
      if (k.toLowerCase() === "content-encoding") return;
      if (k.toLowerCase() === "content-length") return;
      responseHeaders.set(k, v);
    });

    return new NextResponse(responseBody, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { error: "Proxy failed", target: targetUrl, message: msg },
      { status: 502 }
    );
  }
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function PUT(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  const { path } = await context.params;
  return proxy(request, path);
}

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  const { path } = await context.params;
  return proxy(request, path);
}
