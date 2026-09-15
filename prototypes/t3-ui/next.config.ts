import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // Throwaway local prototype. No Prisma, no NextAuth, no hosted auth.
  reactStrictMode: true,
  outputFileTracingRoot: path.resolve(__dirname),
};

export default nextConfig;
