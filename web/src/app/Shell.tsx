import { useEffect, type CSSProperties, type ReactNode } from "react";
import { Link, useLocation, useNavigationType } from "react-router-dom";
import { Mark } from "../components/Mark";
import brand from "../site/brand";
import { DemoBar } from "./DemoBar";
import { useCan, useSession } from "./session";

const ROOM = ["/app/cameras", "/app/watches", "/app/incidents"];

export function Shell({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const { pathname } = useLocation();
  const navigation = useNavigationType();
  const supervisor = useCan("supervisor");

  useEffect(() => {
    if (navigation === "PUSH") window.scrollTo(0, 0);
  }, [pathname, navigation]);

  const nav = [
    { to: "/app", label: "Control room", current: pathname === "/app" || ROOM.some((p) => pathname.startsWith(p)) },
    ...(supervisor ? [{ to: "/app/people", label: "People", current: pathname.startsWith("/app/people") }] : []),
    { to: "/app/settings", label: "Settings", current: pathname.startsWith("/app/settings") },
  ];

  return (
    <div className="app" style={{ "--brand": brand.theme_color } as CSSProperties}>
      <a className="skip" href="#main">Skip to content</a>
      <header className="app-header">
        <div className="wrap">
          <Link to="/app" className="wordmark"><Mark /><span>{brand.name}</span></Link>
          <nav className="app-nav" aria-label="Main">
            {nav.map((n) => (
              <Link key={n.to} to={n.to} aria-current={n.current ? "page" : undefined}>{n.label}</Link>
            ))}
          </nav>
          <Link to="/app/settings/society" className="app-society">{me.workspace?.name}</Link>
        </div>
      </header>
      {me.user.demo && <DemoBar />}
      <main id="main" tabIndex={-1}>{children}</main>
    </div>
  );
}
