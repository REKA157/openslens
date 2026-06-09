import type { NextConfig } from "next";

// On utilise l'IP du VPS sur un port dédié (8001) qui bypass Caddy.
// Caddy redirige HTTP → HTTPS et sert un cert self-signed, ce qui plante
// le proxy serveur de Vercel. Direct sur l'IP+port = pas de Caddy = OK.
const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://2.24.15.60:8001";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        // Frontend appelle /backend/api/dashboard -> backend /api/dashboard
        source: "/backend/:path*",
        destination: `${BACKEND_URL}/:path*`,
      },
    ];
  },
};

export default nextConfig;
