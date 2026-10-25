// Draws public/og.png (1200 by 630) from src/site/OgCard.tsx with the site's own styles and font, in Chromium.
// Run `npm run og` after changing the brand or the control room drawing, and commit the picture.
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";
import { createServer } from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({
  root,
  logLevel: "warn",
  server: { middlewareMode: true },
  appType: "custom",
  optimizeDeps: { noDiscovery: true },
});
const { renderOg } = await vite.ssrLoadModule("/src/site/OgCard.tsx");
const markup = renderOg();
await vite.close();

// fonts go in as data, since a page set from a string cannot load files
const font = async (name) => `data:font/woff2;base64,${(await readFile(`${root}/public/fonts/${name}`)).toString("base64")}`;
const css = [await readFile(`${root}/src/styles.css`, "utf8"), await readFile(`${root}/src/site/site.css`, "utf8")]
  .join("\n")
  .replace("url(/fonts/atkinson-next-latin.woff2)", `url(${await font("atkinson-next-latin.woff2")})`)
  .replace("url(/fonts/atkinson-next-latin-ext.woff2)", `url(${await font("atkinson-next-latin-ext.woff2")})`);

const card = `
  html, body { margin: 0; width: 1200px; height: 630px; overflow: hidden; }
  .site.og { min-height: 0; height: 630px; display: grid; grid-template-columns: 1fr 590px; gap: 56px;
    align-items: center; padding: 0 64px 0 80px; box-sizing: border-box; }
  .site .og-mark { font-size: 64px; gap: 20px; letter-spacing: -0.02em; }
  .site .og-mark .mark { width: 72px; height: 72px; }
  .og-tagline { margin-top: 40px; color: var(--ink); font-size: 50px; font-weight: 700; line-height: 1.1;
    letter-spacing: -0.02em; max-width: 11ch; }
  .og-domain { margin-top: 32px; color: var(--ink-3); font-size: 26px; font-weight: 600; }
  .og .room-wall { grid-template-columns: repeat(3, minmax(0, 1fr)); }
`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
await page.setContent(`<!doctype html><html lang="en"><head><meta charset="utf-8"><style>${css}${card}</style></head>
  <body>${markup}</body></html>`);
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: fileURLToPath(new URL("../public/og.png", import.meta.url)) });
await browser.close();
console.log("wrote public/og.png");
