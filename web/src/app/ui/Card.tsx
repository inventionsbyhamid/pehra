import type { ReactNode } from "react";

export function Card({ title, id, children, flush }: { title?: string; id?: string; children: ReactNode; flush?: boolean }) {
  return (
    <section className={`card${flush ? " card-flush" : ""}`} aria-labelledby={title ? id : undefined}>
      {title && <h2 id={id} className="card-title" style={{ marginBottom: 12 }}>{title}</h2>}
      {children}
    </section>
  );
}

// Label and value rows, as on the site's watch card drawing.
export function Facts({ children, label }: { children: ReactNode; label?: string }) {
  return <dl className="facts" aria-label={label}>{children}</dl>;
}

export function Fact({ term, children, className }: { term: string; children: ReactNode; className?: string }) {
  return (
    <div className={className}>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}
