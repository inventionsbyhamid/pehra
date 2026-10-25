// The 1200 by 630 picture link previews show (public/og.png), drawn by scripts/og.mjs.
import type { CSSProperties } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Mark } from "./art";
import brand from "./brand";
import { ControlRoom } from "./product";

function OgCard() {
  return (
    <div className="site og" style={{ "--brand": brand.theme_color } as CSSProperties}>
      <div className="og-copy">
        <p className="wordmark og-mark">
          <Mark />
          <span>{brand.name}</span>
        </p>
        <p className="og-tagline">{brand.tagline}</p>
        <p className="og-domain">{brand.domain}</p>
      </div>
      <ControlRoom />
    </div>
  );
}

export function renderOg() {
  return renderToStaticMarkup(<OgCard />);
}
