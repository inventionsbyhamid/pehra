import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { api, type Invite } from "../api";
import { Mark } from "../components/Mark";
import { useTitle } from "../hooks";
import brand from "../site/brand";

const ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata";

// The app's header over a narrow page, for someone signed in with no society open yet.
function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="app" style={{ "--brand": brand.theme_color } as CSSProperties}>
      <header className="app-header">
        <div className="wrap">
          <span className="wordmark"><Mark /><span>{brand.name}</span></span>
        </div>
      </header>
      <main id="main" className="wrap page page-narrow">{children}</main>
    </div>
  );
}

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
    <Frame>
      <div className="page-head">
        <div>
          <h1>Name your society to begin.</h1>
          <p className="page-sub">You add cameras and invite your team next. If someone already set up your society, ask them for an invite link instead.</p>
        </div>
      </div>
      <form className="card stack" style={{ gap: 16 }} onSubmit={submit}>
        <label className="field"><span>Society name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required autoFocus
            placeholder="For example: Green Park Residency" />
        </label>
        {error && <p className="notice notice-error" role="alert">{error}</p>}
        <div><button className="btn btn-primary" disabled={busy || !name.trim()}>{busy ? "Saving" : "Continue"}</button></div>
      </form>
    </Frame>
  );
}

// What each role can do, as Settings, Team says it.
const ROLES: Record<string, { label: string; a: string; can: string }> = {
  viewer: { label: "Viewer", a: "a viewer", can: "Sees the cameras and incidents, and asks what happened." },
  supervisor: { label: "Supervisor", a: "a supervisor", can: "Also acknowledges incidents, marks false alarms, and sees the logs and enrolled staff." },
  admin: { label: "Admin", a: "an admin", can: "Also adds cameras and watches, enrols staff, connects Telegram and invites people." },
  owner: { label: "Owner", a: "an owner", can: "Can do everything, including changing roles and removing people." },
};

function inviteProblem(err: unknown): string {
  const code = (err as Error).message;
  if (code === "used") return "This invite was already used. Ask for a new one.";
  if (code === "expired") return "This invite has expired. Ask for a new one.";
  return "This invite link does not work. Ask for a new one.";
}

// An invite link shows the society and the role, and joins only when the person presses Join.
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

  const role = invite ? ROLES[invite.role] : undefined;
  return (
    <Frame>
      <div className="page-head">
        <div>
          <h1>{invite ? `Join ${invite.society}` : "Your invite"}</h1>
          {invite && (
            <p className="page-sub">
              {invite.invited_by ? `${invite.invited_by} invited you` : "You are invited"} to join as {role?.a ?? invite.role}.
            </p>
          )}
        </div>
      </div>
      {error && <p className="notice notice-error" role="alert">{error}</p>}
      {!invite && !error && <p className="working" role="status"><span className="spinner" aria-hidden="true" />Opening your invite</p>}
      {invite && !error && (
        <section className="card stack" style={{ gap: 18 }} aria-label="The invite">
          <dl className="facts">
            <div><dt>Society</dt><dd><strong>{invite.society}</strong></dd></div>
            <div>
              <dt>Your role</dt>
              <dd><span>{role?.label ?? invite.role}</span>{role && <span className="hint" style={{ display: "block" }}>{role.can}</span>}</dd>
            </div>
          </dl>
          <p className="small muted">Nothing changes until you press Join.</p>
          <div className="row">
            <button className="btn btn-primary" onClick={accept} disabled={busy}>{busy ? "Joining" : `Join ${invite.society}`}</button>
            <button className="btn btn-quiet" onClick={onDone}>Not now</button>
          </div>
        </section>
      )}
      {error && <div><button className="btn" onClick={onDone}>Continue</button></div>}
    </Frame>
  );
}
