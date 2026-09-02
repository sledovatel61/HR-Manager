import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Изолированный дизайн-прототип HR Manager.
// Не подключается к backend, не делает внешних runtime-запросов.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    host: "0.0.0.0",
    port: 4173,
    strictPort: true,
    // Позволяет открывать превью за прокси песочницы (Arena live preview).
    allowedHosts: true,
  },
  preview: {
    host: "0.0.0.0",
    port: 4173,
    strictPort: true,
    allowedHosts: true,
  },
});
