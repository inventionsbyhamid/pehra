import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, searchForWorkspaceRoot, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.WATCHFUL_API ?? "http://127.0.0.1:8000";
// one source for the name (brand.json at the repo root) and one for prices (the backend's plans file)
const brandFile = fileURLToPath(new URL("../brand.json", import.meta.url));
const plansFile = fileURLToPath(new URL("../backend/watchful/data/plans.json", import.meta.url));
const readersFile = fileURLToPath(new URL("../backend/watchful/data/readers.json", import.meta.url));
// the docs pages show the diagrams and measured numbers from the same files the README uses
const overviewFile = fileURLToPath(new URL("../docs/overview.svg", import.meta.url));
const architectureFile = fileURLToPath(new URL("../docs/architecture.svg", import.meta.url));
const agentFile = fileURLToPath(new URL("../docs/agent-workflow.svg", import.meta.url));
const benchFile = fileURLToPath(new URL("../eval/results/bench-devbox_6cpu_no_text.json", import.meta.url));
const buildFile = fileURLToPath(new URL("../eval/results/build_measurements.json", import.meta.url));
const brand = JSON.parse(readFileSync(brandFile, "utf8")) as Record<string, string>;

const escape = (s: string) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");

// index.html carries __BRAND_NAME__ style placeholders for the app shell's title and tags
const brandHtml: Plugin = {
  name: "brand-html",
  transformIndexHtml: (html) =>
    html.replace(/__BRAND_([A-Z_]+)__/g, (_, key: string) => escape(brand[key.toLowerCase()] ?? "")),
};

export default defineConfig({
  plugins: [react(), brandHtml],
  resolve: {
    alias: {
      "@data/brand": brandFile,
      "@data/plans": plansFile,
      "@data/readers": readersFile,
      "@docs/overview.svg": overviewFile,
      "@docs/architecture.svg": architectureFile,
      "@docs/agent-workflow.svg": agentFile,
      "@results/bench": benchFile,
      "@results/build": buildFile,
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    fs: {
      allow: [
        searchForWorkspaceRoot(process.cwd()),
        brandFile,
        plansFile,
        readersFile,
        overviewFile,
        architectureFile,
        agentFile,
        benchFile,
        buildFile,
      ],
    },
    proxy: {
      "/api": api,
      "/auth": api,
      "/healthz": api,
      "/ws": { target: api, ws: true },
    },
  },
  // cam.html: the device page, its own small build (served with its own policy, see backend api/device.py)
  build: { outDir: "dist", sourcemap: false, rollupOptions: { input: { index: "index.html", cam: "cam.html" } } },
});
