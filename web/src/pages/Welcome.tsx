import { useEffect, useState } from "react";
import { api } from "../api";
import { Mark } from "../components/Mark";
import { useTitle } from "../hooks";

const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata";

export default function Welcome({ onDone }: { onDone: () => void }) {
  useTitle("Name your society");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createSociety(name.trim(), ZONE);
      onDone();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <main className="login" id="main">
      <div className="login-copy">
        <div className="brand"><Mark size={36} /><span className="brand-name">Watchful</span></div>
        <h1 className="login-title">Name your society to begin.</h1>
        <p className="lede">You can add cameras and invite your team next. If someone already set up your society, ask them for an invite link instead.</p>
      </div>
      <form className="panel login-form stack" onSubmit={submit}>
        <label className="field"><span>Society name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required autoFocus />
        </label>
        {error && <p className="notice notice-error" role="alert">{error}</p>}
        <button className="btn btn-primary" disabled={busy || !name.trim()}>{busy ? "Saving" : "Continue"}</button>
      </form>
    </main>
  );
}

export function Join({ token, onDone }: { token: string; onDone: () => void }) {
  useTitle("Join your society");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.acceptInvite(token).then(onDone).catch((err) => setError(
      (err as Error).message === "used" ? "This invite was already used. Ask for a new one." :
        (err as Error).message === "expired" ? "This invite has expired. Ask for a new one." :
          "This invite link does not work. Ask for a new one."));
  }, [token]);

  return (
    <main className="login" id="main">
      <div className="panel login-form stack">
        <h1 className="h2">Joining your society</h1>
        {error ? <p className="notice notice-error" role="alert">{error}</p> : <p className="small">One moment.</p>}
        {error && <button className="btn" onClick={onDone}>Continue</button>}
      </div>
    </main>
  );
}
