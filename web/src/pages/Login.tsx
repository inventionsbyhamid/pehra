import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Mark } from "../components/Mark";
import { useTitle } from "../hooks";

const ERRORS: Record<string, string> = {
  signin: "Sign in did not finish. Try again.",
  cancelled: "Sign in was cancelled.",
  setup: "Sign in with that account is not set up on this server yet.",
  link: "That sign in link has expired or was already used. Ask for a new one.",
  busy: "Too many tries from this address. Wait a minute and try again.",
};

export default function Login() {
  useTitle("Sign in");
  const [providers, setProviders] = useState<{ name: string; label: string }[] | null>(null);
  const code = new URLSearchParams(window.location.search).get("error");
  const error = code ? ERRORS[code] ?? ERRORS.signin : null;

  useEffect(() => {
    api.providers().then((r) => setProviders(r.providers)).catch(() => setProviders([]));
  }, []);

  const next = window.location.pathname.startsWith("/login") ? "/" : window.location.pathname + window.location.search;

  return (
    <main className="login" id="main">
      <div className="login-copy">
        <div className="brand"><Mark size={36} /><span className="brand-name">Watchful</span></div>
        <h1 className="login-title">Your cameras, watched for the moments that matter.</h1>
        <p className="lede">Say what to look out for in plain words: someone falls, a car comes into the drive, a person at the back door at night. OpenCV watches every frame, an AI checks each flagged moment, and you can ask what happened.</p>
      </div>
      <div className="panel login-form stack">
        <h2 className="h2">Sign in</h2>
        {error && <p className="notice notice-error" role="alert">{error}</p>}
        {(providers ?? []).map((p) => (
          <a key={p.name} className="btn btn-primary" href={`/auth/${p.name}/start?next=${encodeURIComponent(next)}`}>
            Continue with {p.label}
          </a>
        ))}
        {providers !== null && providers.length === 0 && (
          <p className="small">Ask the person who runs your society's account for a sign in link.</p>
        )}
        <p className="xsmall muted">A sign in link from your society's admin works too. <Link to="/terms">Terms and privacy</Link></p>
      </div>
    </main>
  );
}
