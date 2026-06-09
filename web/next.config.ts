import type { NextConfig } from "next";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ||
  "http://izomje1iggly4e23t8xr6p7v.2.24.15.60.sslip.io";

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
