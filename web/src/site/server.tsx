// Entry for scripts/prerender.mjs: renders the public pages to HTML at build time.
import { StrictMode, type ComponentType } from "react";
import { renderToString } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import brand, { site } from "./brand";
import { AwsDocs, DocsHome, GettingStarted, HowItWorks, PrivacySafety, SelfHosting } from "./DocsPages";
import Landing from "./Landing";
import Login from "./Login";
import { PAGES } from "./pages";
import Pricing from "./Pricing";
import Privacy from "./Privacy";
import Refunds from "./Refunds";
import Terms from "./Terms";

const VIEWS: Record<string, ComponentType> = {
  "/": Landing,
  "/pricing": Pricing,
  "/terms": Terms,
  "/privacy": Privacy,
  "/refunds": Refunds,
  "/login": Login,
  "/docs": DocsHome,
  "/docs/getting-started": GettingStarted,
  "/docs/how-it-works": HowItWorks,
  "/docs/privacy-and-safety": PrivacySafety,
  "/docs/self-hosting": SelfHosting,
  "/docs/aws": AwsDocs,
};

export const pages = PAGES;
export { brand, site };

export function render(path: string) {
  const View = VIEWS[path];
  if (!View) throw new Error(`no prerendered view for ${path}`);
  return renderToString(
    <StrictMode>
      <StaticRouter location={path}>
        <View />
      </StaticRouter>
    </StrictMode>,
  );
}
