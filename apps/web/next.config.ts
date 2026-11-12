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
};

export default nextConfig;
