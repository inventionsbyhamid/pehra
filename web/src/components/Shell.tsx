import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../api";
import { useSession } from "../App";
import { useLoad } from "../hooks";
import { Mark } from "./Mark";

const NAV = [
  { to: "/", label: "Cameras" },
  { to: "/alerts", label: "Alerts" },
  { to: "/ask", label: "Ask" },
  { to: "/find", label: "Find a person" },
  { to: "/people", label: "People" },
  { to: "/numbers", label: "Numbers" },
];

function isCurrent(to: string, path: string) {
  if (to === "/") return path === "/" || path.startsWith("/cameras");
  return path.startsWith(to);
}

export function Shell({ children }: { children: ReactNode }) {
  const { user, setUser } = useSession();
  const { pathname } = useLocation();
  const alerts = useLoad(() => api.alerts(), [], 15000);
  const waiting = (alerts.data ?? []).filter((a) => a.status === "awaiting_human" || a.status === "unverified").length;

  async function signOut() {
    await api.logout().catch(() => {});
    setUser(null);
  }

  return (
    <div className="shell">
      <a className="skip" href="#main">Skip to content</a>
      <header className="rail">
        <Link to="/" className="brand" aria-label="Watchful, go to cameras">
          <Mark />
          <span className="brand-name">Watchful</span>
        </Link>
        <nav className="nav" aria-label="Main">
          {NAV.map((n) => (
            <Link key={n.to} to={n.to} aria-current={isCurrent(n.to, pathname) ? "page" : undefined}>
              <span>{n.label}</span>
              {n.to === "/alerts" && waiting > 0 && (
                <span className="nav-count" title={`${waiting} alerts need a look`}>
                  {waiting}<span className="sr-only"> need a look</span>
                </span>
              )}
            </Link>
          ))}
          <Link to="/terms" className="nav-extra" aria-current={pathname === "/terms" ? "page" : undefined}>Privacy</Link>
          <button type="button" className="nav-extra nav-button" onClick={signOut}>Sign out</button>
        </nav>
        <div className="rail-foot">
          <Link to="/terms" aria-current={pathname === "/terms" ? "page" : undefined}>Terms and privacy</Link>
          <span className="rail-user">Signed in as {user}</span>
          <button type="button" className="nav-button" onClick={signOut}>Sign out</button>
        </div>
      </header>
      <main id="main" className="main" tabIndex={-1}>{children}</main>
    </div>
  );
}
