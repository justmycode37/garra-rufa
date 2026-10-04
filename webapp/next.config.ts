import type { NextConfig } from 'next';
const config: NextConfig = {
  output: 'standalone',
  devIndicators: false,
  logging: { incomingRequests: { ignore: [/\/api\/auth\/(?:chatgpt\/)?callback/] } },
  outputFileTracingExcludes: {'*':['./data/**/*','./.env*','./tests/**/*']},
  // the graph chat reads the bundled example graphs' reports
  outputFileTracingIncludes: {'/api/search':['./public/graph-data/index.json','./public/graph-data/*.md']},
  serverExternalPackages: ['node:sqlite'],
  async headers() {
    return [{ source: '/:path*', headers: [
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'X-Frame-Options', value: 'DENY' },
      { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
      { key: 'Permissions-Policy', value: 'camera=(), microphone=(self), geolocation=()' }
    ] }];
  }
};
export default config;
