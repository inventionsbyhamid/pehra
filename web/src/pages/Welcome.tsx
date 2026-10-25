import { useEffect, useState } from "react";
import { api, type Invite } from "../api";
import { Mark } from "../components/Mark";
import { useTitle } from "../hooks";
import brand from "../site/brand";

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
        <div className="brand"><Mark size={36} /><span className="brand-name">{brand.name}</span></div>
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

const ROLES: Record<string, string> = {
  viewer: "a viewer: you can see the cameras, alerts and the log, and ask what happened",
  supervisor: "a supervisor: you can act on alerts and set lookouts",
  admin: "an admin: you can manage cameras, watches, staff and invites",
  owner: "an owner: you can do everything, including changing roles",
};

function inviteProblem(err: unknown): string {
  const code = (err as Error).message;
  if (code === "used") return "This invite was already used. Ask for a new one.";
  if (code === "expired") return "This invite has expired. Ask for a new one.";
  return "This invite link does not work. Ask for a new one.";
}

export function Join({ token, onDone }: { token: string; onDone: () => void }) {
  useTitle("Join your society");
  const [invite, setInvite] = useState<Invite | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.invite(token).then(setInvite).catch((err) => setError(inviteProblem(err)));
  }, [token]);

  async function accept() {
    setBusy(true);
    setError(null);
    try {
      await api.acceptInvite(token);
      onDone();
    } catch (err) {
      setError(inviteProblem(err));
      setBusy(false);
    }
  }

  return (
    <main className="login" id="main">
      <div className="login-copy">
        <div className="brand"><Mark size={36} /><span className="brand-name">{brand.name}</span></div>
      </div>
      <div className="panel login-form stack">
        {invite ? (
          <>
            <h1 className="h2">Join {invite.society}</h1>
            <p>{invite.invited_by ? `${invite.invited_by} invited you` : "You are invited"} to join as {ROLES[invite.role] ?? invite.role}.</p>
          </>
        ) : <h1 className="h2">Your invite</h1>}
        {error && <p className="notice notice-error" role="alert">{error}</p>}
        {!invite && !error && <p className="small">One moment.</p>}
        {invite && !error && <button className="btn btn-primary" onClick={accept} disabled={busy}>{busy ? "Joining" : `Join ${invite.society}`}</button>}
        <button className="btn" onClick={onDone}>{invite && !error ? "Not now" : "Continue"}</button>
      </div>
    </main>
  );
}
