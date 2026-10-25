import { useEffect, type CSSProperties, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigationType } from "react-router-dom";
import { APP_URL, TryDemo } from "./actions";
import { Mark } from "./art";
import brand, { mailto } from "./brand";
import { pageMeta } from "./pages";
import { noSignIn, useProviders, useVisitor } from "./providers";
import "./site.css";

// members signed in go back to the control room; the prerendered page shows the signed out links until it knows
function HeaderActions() {
  const who = useVisitor();
  const none = noSignIn(useProviders());
  if (who === "member" || who === "demo")
    return (
      <a href={APP_URL} className="cta cta-small">
        {who === "member" ? "Open the control room" : "Open your demo"}
      </a>
    );
  if (none) return <TryDemo className="cta cta-small" />;
  return (
    <>
      <Link to="/login" className="nav-signin">
        Sign in
      </Link>
      <Link to="/login" className="cta cta-small">
        Start free
      </Link>
    </>
  );
}

export function Wordmark() {
  return (
    <>
      <Mark />
      <span>{brand.name}</span>
    </>
  );
}

export default function SiteLayout({ children }: { children: ReactNode }) {
  const { pathname, hash } = useLocation();
  const none = noSignIn(useProviders());
  const navigation = useNavigationType();

  useEffect(() => {
    const meta = pageMeta(pathname);
    if (meta) document.title = meta.title;
  }, [pathname]);

  useEffect(() => {
    if (navigation !== "PUSH") return;
    const target = hash ? document.getElementById(decodeURIComponent(hash.slice(1))) : null;
    if (target) target.scrollIntoView();
    else window.scrollTo(0, 0);
  }, [pathname, hash, navigation]);

  return (
    <div className="site" style={{ "--brand": brand.theme_color } as CSSProperties}>
      <a className="site-skip" href="#content">
        Skip to content
      </a>
      <header className="site-header">
        <div className="wrap header-row">
          <Link to="/" className="wordmark" aria-label={`${brand.name} home`}>
            <Wordmark />
          </Link>
          <nav aria-label="Main" className="site-nav">
            <Link to="/#how" className="nav-more">
              How it works
            </Link>
            <NavLink to="/pricing" className="nav-wide">
              Pricing
            </NavLink>
            <NavLink to="/privacy" className="nav-more">
              Privacy
            </NavLink>
            <HeaderActions />
          </nav>
        </div>
      </header>
      <main id="content" tabIndex={-1}>
        {children}
      </main>
      <footer className="site-footer">
        <div className="wrap footer-grid">
          <div>
            <p className="wordmark footer-mark">
              <Wordmark />
            </p>
            <p className="footer-line">{brand.tagline}</p>
          </div>
          <nav aria-label="Footer">
            <ul className="footer-links">
              <li>
                <Link to="/pricing">Pricing</Link>
              </li>
              <li>
                <Link to="/privacy">Privacy</Link>
              </li>
              <li>
                <Link to="/terms">Terms</Link>
              </li>
              <li>
                <Link to="/refunds">Refunds</Link>
              </li>
              {!none && (
                <li>
                  <Link to="/login">Sign in</Link>
                </li>
              )}
              <li>
                <a href={mailto()}>{brand.contact_email}</a>
              </li>
            </ul>
          </nav>
          <p className="footer-small">
            {brand.name} is run by {brand.operator}, {brand.country}. Prices are in rupees and exclude GST.
          </p>
        </div>
      </footer>
    </div>
  );
}
