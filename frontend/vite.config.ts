import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The API base URL is configurable at build time via VITE_API_BASE_URL and
// falls back to "/api", which nginx proxies to the backend (see nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    // Dev-only convenience: allow any host so the server also works behind
    // preview proxies (e.g. Arena live preview). Production builds are served
    // by nginx and are unaffected by this setting.
    allowedHosts: true,
    proxy: {
      "/api": {
        target: process.env.HR_API_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
    globals: true,
  },
});
