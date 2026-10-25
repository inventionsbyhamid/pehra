import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function Page({ children, narrow }: { children: ReactNode; narrow?: boolean }) {
  return <div className={`wrap page${narrow ? " page-narrow" : ""}`}>{children}</div>;
}

export function PageHead({ title, back, sub, actions }: {
  title: ReactNode;
  back?: { to: string; label: string };
  sub?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div style={{ minWidth: 0 }}>
        {back && <Link to={back.to} className="back">{back.label}</Link>}
        <h1>{title}</h1>
        {sub && <div className="page-sub">{sub}</div>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Section({ title, id, aside, children }: { title: string; id: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="section" aria-labelledby={id}>
      <div className="section-head">
        <h2 id={id}>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <p><strong>{title}</strong></p>
      {children && <p className="small">{children}</p>}
      {action}
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return <p className="working" role="status"><span className="spinner" aria-hidden="true" />{what}</p>;
}

export function Problem({ children }: { children: ReactNode }) {
  return children ? <p className="notice notice-error" role="alert">{children}</p> : null;
}
