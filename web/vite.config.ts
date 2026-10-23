import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.WATCHFUL_API ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": api,
      "/blobs": api,
      "/healthz": api,
      "/ws": { target: api, ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
