import { Link } from "react-router-dom";
import { noSignIn, useDemoOffered, useProviders, useVisitor } from "./providers";

export const DEMO_URL = "/demo";
export const APP_URL = "/app";

// "Try the demo": a sandbox society with sample cameras and no sign in. A full page load, so the server starts it.
// Nothing when this server does not offer the demo.
export function TryDemo({ className, label = "Try the demo" }: { className: string; label?: string }) {
  if (!useDemoOffered()) return null;
  return (
    <a className={className} href={DEMO_URL}>
      {label}
    </a>
  );
}

// A sign up button ("Start free", or a plan's), or "Try the demo" when this server offers no way to sign in, so the
// button never leads to a dead end.
export function StartFree({ className, to = "/login", label = "Start free" }: {
  className: string;
  to?: string;
  label?: string;
}) {
  if (noSignIn(useProviders())) return <TryDemo className={className} />;
  return (
    <Link to={to} className={className}>
      {label}
    </Link>
  );
}

// The main pair of buttons: the control room first for a member signed in, their demo for a demo visitor, and for
// anyone else start free next to try the demo (the demo alone when this server has no way to sign in).
export function StartActions() {
  const who = useVisitor();
  const none = noSignIn(useProviders());
  if (who === "member" || who === "demo") {
    return (
      <>
        <a href={APP_URL} className="cta">
          {who === "member" ? "Open the control room" : "Open your demo"}
        </a>
        {who === "demo" && !none && (
          <Link to="/login" className="cta cta-line">
            Start free
          </Link>
        )}
      </>
    );
  }
  if (none) return <TryDemo className="cta" />;
  return (
    <>
      <Link to="/login" className="cta">
        Start free
      </Link>
      <TryDemo className="cta cta-line" />
    </>
  );
}
