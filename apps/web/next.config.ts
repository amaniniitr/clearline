import type { NextConfig } from "next";

// The console is a client-side app talking to the FastAPI backend; no prerendering or cache components needed.
const nextConfig: NextConfig = {
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_API || "https://de57e790d6bca0.lhr.life";
    return [
      {
        source: "/api/:path*",
        destination: `${backend}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
