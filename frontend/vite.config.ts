import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Vite dev-сервер фронтенда проксирует health-запросы на backend.
// В docker compose цель задаётся переменной VITE_PROXY_TARGET (http://backend:8000);
// при локальном запуске без docker используется localhost:8000.
const proxyTarget = process.env.VITE_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    // Preview-среды обращаются к dev-серверу с нестандартного хоста;
    // без явного разрешения Vite отвечает 403 (blocked host).
    allowedHosts: [".e2b.app", "localhost", "127.0.0.1"],
    proxy: {
      "/health": {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    clearMocks: true,
  },
});
