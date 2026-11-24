import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The dashboard is interactive and reads live data from the API Gateway
  // backend, so Cache Components / partial prefetching are explicitly off.
  cacheComponents: false,
  partialPrefetching: false,
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
