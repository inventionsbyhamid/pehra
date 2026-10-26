import type { ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import bench from "@results/bench";
import build, { type Measured } from "@results/build";
import SiteLayout from "./SiteLayout";

// The public documentation under /docs. Each page is prerendered like the legal pages (pages.ts, server.tsx).
export const DOCS = [
  { path: "/docs", label: "Overview" },
  { path: "/docs/getting-started", label: "Getting started" },
  { path: "/docs/how-it-works", label: "How it works" },
  { path: "/docs/privacy-and-safety", label: "Privacy and safety" },
  { path: "/docs/self-hosting", label: "Self hosting" },
  { path: "/docs/aws", label: "AWS" },
] as const;

export const REPO = "https://github.com/inventionsbyhamid/pehra";
export const repoFile = (path: string) => `${REPO}/blob/main/${path}`;

export function DocsPage({ path, title, lead, children }: {
  path: (typeof DOCS)[number]["path"];
  title: string;
  lead: ReactNode;
  children: ReactNode;
}) {
  const next = DOCS[DOCS.findIndex((d) => d.path === path) + 1];
  return (
    <SiteLayout>
      <div className="wrap docs">
        <nav className="docs-nav" aria-label="Documentation">
          <p className="docs-nav-title">Docs</p>
          <ul>
            {DOCS.map((d) => (
              <li key={d.path}>
                <NavLink to={d.path} end>
                  {d.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <article className="docs-body">
          <h1>{title}</h1>
          <div className="docs-lead">{lead}</div>
          {children}
          {next && (
            <p className="docs-next">
              Next: <Link to={next.path}>{next.label}</Link>
            </p>
          )}
        </article>
      </div>
    </SiteLayout>
  );
}

// a command block that scrolls on its own on a narrow screen, and can be reached by keyboard to scroll it
export function Code({ children, label }: { children: string; label: string }) {
  return (
    <pre className="docs-code" tabIndex={0} aria-label={label}>
      <code>{children.trim()}</code>
    </pre>
  );
}

const plain = (v: number) => (Math.abs(v) >= 10 ? Math.round(v).toLocaleString("en-US") : String(+v.toFixed(2)));

// a measured number with its unit, as docs/fill.py writes it, or "not measured yet"
function words(m: Measured | undefined) {
  if (!m || typeof m.value !== "number") return "not measured yet";
  if (m.unit === "mb") return `${Math.round(m.value).toLocaleString("en-US")} MB`;
  if (m.unit === "usd") return `$${m.value.toFixed(2)}`;
  if (m.unit === "s") return `${plain(m.value)} s`;
  return plain(m.value);
}

const measured = (group: string, key: string) => (build[group] as Record<string, Measured> | undefined)?.[key];

export const MEASURED = {
  benchLabel: bench.label,
  benchCameras: bench.scenario.cameras,
  benchCpus: bench.scenario.vcpus,
  benchFps: `${plain(bench.fps.per_camera.median)} frames a second`,
  benchMemory: `${Math.round(bench.memory_mb.total_median).toLocaleString("en-US")} MB`,
  imageSize: words(measured("selfhost", "image_mb")),
  modelsSize: words(measured("selfhost", "models_mb")),
  awsMonth: words(measured("estimates", "aws_month_usd")),
};
