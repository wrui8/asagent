import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发时把 /api 代理到本地 FastAPI；生产由 Caddy 同源反代
const apiTarget = process.env.AS_API_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  // PWA 由 public/manifest.webmanifest + public/sw.js 手写实现，
  // 避免 vite-plugin-pwa 的 workbox-build 在 Node 18 ESM 下的兼容问题。
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
