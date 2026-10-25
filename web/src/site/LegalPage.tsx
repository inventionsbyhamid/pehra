import type { ReactNode } from "react";
import SiteLayout from "./SiteLayout";

export const UPDATED = "25 October 2026";

export default function LegalPage({ title, intro, children }: { title: string; intro: ReactNode; children: ReactNode }) {
  return (
    <SiteLayout>
      <article className="wrap legal">
        <h1>{title}</h1>
        <p className="legal-updated">Last updated {UPDATED}</p>
        <div className="legal-intro">{intro}</div>
        <div className="legal-body">{children}</div>
      </article>
    </SiteLayout>
  );
}
