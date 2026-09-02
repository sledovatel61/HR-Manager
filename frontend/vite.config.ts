/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// Тесты живут внутри src рядом с кодом (Vitest), e2e — на верхнем уровне репозитория.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  // Адрес backend для dev-прокси (браузер никогда не ходит на backend
  // напрямую: и dev-сервер Vite, и nginx в Docker отдают /health и /api/*
  // с того же origin — это осознанное решение, CORS не нужен).
  const proxyTarget = env.VITE_BACKEND_URL ?? "http://localhost:8000";

  // Проброс хостов для предпросмотра в песочницах: VITE_ALLOWED_HOSTS=".example.dev"
  // (ведущая точка разрешает домен и поддомены), "*" — разрешить все.
  const rawHosts = (env.VITE_ALLOWED_HOSTS ?? "")
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);
  const allowedHosts = rawHosts.includes("*") ? true : rawHosts.length > 0 ? rawHosts : undefined;

  return {
    plugins: [react()],
    server: {
      port: 5173,
      allowedHosts,
      proxy: {
        "/health": { target: proxyTarget, changeOrigin: true },
        "/api": { target: proxyTarget, changeOrigin: true },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
      css: false,
    },
  };
});
