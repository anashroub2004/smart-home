import type { NextConfig } from "next";

// Static export: `npm run build` produces web/out/, which Firebase Hosting serves.
// No server code — all data comes from Firebase directly in the browser.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,
};

export default nextConfig;
