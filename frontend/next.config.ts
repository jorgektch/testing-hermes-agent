import type { NextConfig } from 'next';
const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Optional split-terminal local development without Vercel CLI:
  // LOCAL_API_URL=http://127.0.0.1:8000 npm run dev
  async rewrites() {
    return process.env.LOCAL_API_URL
      ? [{ source: '/api/:path*', destination: `${process.env.LOCAL_API_URL}/api/:path*` }]
      : [];
  },
};
export default nextConfig;
