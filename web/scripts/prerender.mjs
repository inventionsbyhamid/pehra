// Renders the public pages to static HTML after `vite build`, so they work without JavaScript and can be crawled.
// Writes dist/index.html (landing), dist/<page>.html and dist/<page>/index.html, and keeps the plain app shell as
// dist/app.html for routes that are not prerendered. main.tsx hydrates only when data-prerendered matches the route.
// Links in the tags are absolute, on brand.json's domain unless SITE_URL says otherwise.
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = path.join(root, "dist");
const out = path.join(root, "node_modules", ".cache", "prerender");

const escape = (s) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function headTags(page, brand, base) {
  const url = `${base}${page.path}`;
  const image = `${base}/og.png`;
  const meta = (key, name, content) => `<meta ${key}="${name}" content="${escape(content)}" />`;
  return [
    meta("name", "description", page.description),
    page.noindex && meta("name", "robots", "noindex"),
    `<link rel="canonical" href="${escape(url)}" />`,
    `<link rel="preload" href="/fonts/atkinson-next-latin.woff2" as="font" type="font/woff2" crossorigin />`,
    meta("property", "og:type", "website"),
    meta("property", "og:site_name", brand.name),
    meta("property", "og:locale", "en_IN"),
    meta("property", "og:title", page.title),
    meta("property", "og:description", page.description),
    meta("property", "og:url", url),
    meta("property", "og:image", image),
    meta("property", "og:image:width", "1200"),
    meta("property", "og:image:height", "630"),
    meta("property", "og:image:alt", `${brand.name}: ${brand.tagline}`),
    meta("name", "twitter:card", "summary_large_image"),
    meta("name", "twitter:title", page.title),
    meta("name", "twitter:description", page.description),
    meta("name", "twitter:image", image),
  ]
    .filter(Boolean)
    .join("\n    ");
}

function fill(template, page, html, brand, base) {
  const out = template
    .replace(/<title>[^<]*<\/title>/, () => `<title>${escape(page.title)}</title>`)
    .replace(/\s*<meta name="description"[^>]*>/, "")
    .replace("</head>", () => `  ${headTags(page, brand, base)}\n  </head>`)
    .replace('<div id="root"></div>', () => `<div id="root" data-prerendered="${page.path}">${html}</div>`);
  if (!out.includes(html)) throw new Error(`could not place ${page.path} into index.html`);
  return out;
}

await build({
  root,
  logLevel: "warn",
  build: {
    ssr: "src/site/server.tsx",
    outDir: out,
    emptyOutDir: true,
    copyPublicDir: false,
    rollupOptions: { output: { entryFileNames: "server.mjs" } },
  },
});

const { render, pages, brand, site } = await import(pathToFileURL(path.join(out, "server.mjs")).href);
const base = (process.env.SITE_URL || site).replace(/\/+$/, "");
const template = await readFile(path.join(dist, "index.html"), "utf8");
await writeFile(path.join(dist, "app.html"), template);

for (const page of pages) {
  const html = fill(template, page, render(page.path), brand, base);
  if (page.path === "/") {
    await writeFile(path.join(dist, "index.html"), html);
  } else {
    const name = page.path.slice(1);
    await mkdir(path.dirname(path.join(dist, name)), { recursive: true }); // /docs/aws sits in dist/docs
    await writeFile(path.join(dist, `${name}.html`), html);
    await mkdir(path.join(dist, name), { recursive: true });
    await writeFile(path.join(dist, name, "index.html"), html);
  }
  console.log(`prerendered ${page.path}`);
}

const listed = pages.filter((p) => !p.noindex);
const urls = listed.map((p) => `  <url><loc>${escape(base + p.path)}</loc></url>`).join("\n");
await writeFile(
  path.join(dist, "sitemap.xml"),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`,
);
const robots = await readFile(path.join(dist, "robots.txt"), "utf8");
await writeFile(path.join(dist, "robots.txt"), `${robots.trimEnd()}\n\nSitemap: ${base}/sitemap.xml\n`);

await rm(out, { recursive: true, force: true });
