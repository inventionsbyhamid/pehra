import { useState } from "react";
import { api } from "../api";
import { at, epoch } from "../format";
import { useLoad } from "../hooks";
import type { Member, Role } from "../types";
import { atLeast, useSession } from "./session";
import { Copy } from "./ui/Copy";
import { Problem } from "./ui/Page";
import { Table } from "./ui/Table";

const ROLES: { role: Role; label: string; can: string }[] = [
  { role: "viewer", label: "Viewer", can: "Sees the cameras, incidents and logs, and asks what happened." },
  { role: "supervisor", label: "Supervisor", can: "Also acknowledges incidents, marks false alarms and sees enrolled staff." },
  { role: "admin", label: "Admin", can: "Also adds cameras and watches, enrols staff, connects Telegram and invites people." },
  { role: "owner", label: "Owner", can: "Also changes roles and removes people." },
];

const label = (r: Role) => ROLES.find((x) => x.role === r)?.label ?? r;

const PROBLEMS: Record<string, string> = {
  last_owner: "A society needs at least one owner. Make someone else an owner first.",
};

function MemberRow({ m, owner, onChange }: { m: Member; owner: boolean; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const who = m.name || m.email || "A member";
  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      const msg = (e as Error).message;
      setError(PROBLEMS[msg] ?? msg);
    }
  }
  return (
    <li>
      <div className="grow">
        <p><strong>{who}</strong>{m.you && <span className="muted"> (you)</span>}</p>
        {m.name && m.email && <p className="small muted">{m.email}</p>}
        <Problem>{error}</Problem>
      </div>
      {owner && !m.you ? (
        <>
          <select className="select" style={{ width: "auto" }} value={m.role} aria-label={`Role of ${who}`}
            onChange={(e) => run(() => api.changeRole(m.user_id, e.target.value as Role))}>
            {ROLES.map((r) => <option key={r.role} value={r.role}>{r.label}</option>)}
          </select>
          <button className="btn btn-sm btn-quiet btn-danger" onClick={() => run(() => api.removeMember(m.user_id))}>
            Remove<span className="sr-only"> {who}</span>
          </button>
        </>
      ) : (
        <span className="small">{label(m.role)}</span>
      )}
    </li>
  );
}

export function Team() {
  const { me } = useSession();
  const admin = atLeast(me.role, "admin");
  const owner = atLeast(me.role, "owner");
  const team = useLoad(() => (admin ? api.members() : Promise.resolve(null)), [admin]);
  const [role, setRole] = useState<Role>("supervisor");
  const [link, setLink] = useState<{ url: string; role: Role; expires_at: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const made = await api.makeInvite(role);
      setLink({ url: made.url, role, expires_at: made.invite.expires_at });
      team.reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    await api.revokeInvite(id).catch((e) => setError((e as Error).message));
    team.reload();
  }

  return (
    <>
      <h1>Team</h1>
      {admin ? (
        <>
          <section className="section" aria-labelledby="members-title">
            <h2 id="members-title" className="h2">People in {me.workspace?.name}</h2>
            <Problem>{team.error}</Problem>
            {team.data && (
              <ul className="list card people-rows">
                {team.data.members.map((m) => <MemberRow key={m.user_id} m={m} owner={owner} onChange={team.reload} />)}
              </ul>
            )}
          </section>

          <section className="card stack" aria-labelledby="invite-title">
            <h2 id="invite-title" className="h2">Invite someone</h2>
            <form className="row" style={{ alignItems: "flex-end" }} onSubmit={invite}>
              <label className="field">
                <span>As</span>
                <select className="select" value={role} onChange={(e) => setRole(e.target.value as Role)}>
                  {ROLES.filter((r) => atLeast(me.role, r.role)).map((r) => <option key={r.role} value={r.role}>{r.label}</option>)}
                </select>
              </label>
              <button className="btn btn-primary" disabled={busy}>{busy ? "Making the link" : "Make an invite link"}</button>
            </form>
            <Problem>{error}</Problem>
            {link && (
              <div className="stack" style={{ gap: 8 }}>
                <p className="small">Send this link to the person you are inviting as {label(link.role).toLowerCase()}. It works once, until {at(epoch(link.expires_at))}.</p>
                <Copy value={link.url} label="Invite link" />
              </div>
            )}
            {team.data && team.data.invites.length > 0 && (
              <div className="stack" style={{ gap: 6 }}>
                <h3 className="h3">Invites not used yet</h3>
                <ul className="list people-rows">
                  {team.data.invites.map((i) => (
                    <li key={i.id}>
                      <span className="grow small">{label(i.role)}, works until {at(epoch(i.expires_at))}</span>
                      <button className="btn btn-sm btn-quiet" onClick={() => revoke(i.id)}>Cancel invite</button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </>
      ) : (
        <p className="notice">Only an admin can see the team and invite people. Ask your society's admin.</p>
      )}

      <section className="section" aria-labelledby="roles-title">
        <h2 id="roles-title" className="h2">What each role can do</h2>
        <div className="card roles" style={{ padding: "8px 20px" }}>
          <Table caption="What each role can do" head={["Role", "Can"]}>
            {ROLES.map((r) => (
              <tr key={r.role}>
                <th scope="row">{r.label}</th>
                <td>{r.can}</td>
              </tr>
            ))}
          </Table>
        </div>
      </section>
    </>
  );
}
