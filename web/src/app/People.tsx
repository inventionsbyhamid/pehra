import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useLoad, useTitle } from "../hooks";
import brand from "../site/brand";
import type { StaffList, StaffMember } from "../types";
import { useCan } from "./session";
import { Dialog, useDialog } from "./ui/Dialog";
import { Empty, Page, PageHead, Problem } from "./ui/Page";
import { sentence } from "./words";

const agreed = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : null;

type Pick = { file: File; url: string };

function EnrolForm({ form, onDone }: { form: StaffList; onDone: (name: string) => void }) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [photos, setPhotos] = useState<Pick[]>([]);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const kept = useRef<Pick[]>([]);
  kept.current = photos;
  const { min, max } = form.photos;
  const who = name.trim() || "This person";

  useEffect(() => () => kept.current.forEach((p) => URL.revokeObjectURL(p.url)), []);

  function add(files: FileList | null) {
    const more = Array.from(files ?? []).slice(0, Math.max(0, max - photos.length))
      .map((file) => ({ file, url: URL.createObjectURL(file) }));
    setPhotos([...photos, ...more]);
    if (input.current) input.current.value = "";
  }

  function drop(i: number) {
    URL.revokeObjectURL(photos[i]!.url);
    setPhotos(photos.filter((_, j) => j !== i));
  }

  async function enrol(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !consent || photos.length < min || !name.trim() || !role) return;
    setBusy(true);
    setError(null);
    const body = new FormData();
    body.append("name", name.trim());
    body.append("role", role);
    body.append("consent", "true");
    photos.forEach((p) => body.append("photos", p.file));
    try {
      const r = await api.enrolStaff(body);
      photos.forEach((p) => URL.revokeObjectURL(p.url));
      setPhotos([]);
      setName("");
      setRole("");
      setConsent(false);
      onDone(r.name);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card stack" style={{ gap: 18 }} onSubmit={enrol} aria-labelledby="enrol-title">
      <div className="stack" style={{ gap: 8 }}>
        <h2 id="enrol-title" className="h2">Enrol a staff member</h2>
        <ul className="list rules">
          <li>Adults only, with their consent. Never enrol a child.</li>
          <li>No photo is kept. Each photo becomes a face template, then the photo is dropped.</li>
        </ul>
      </div>
      {form.notice ? (
        <p className="notice notice-warn" role="status">{form.notice}</p>
      ) : (
        <>
          <label className="field"><span>Name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required autoComplete="off" />
          </label>
          <label className="field"><span>Role</span>
            <select className="select" value={role} onChange={(e) => setRole(e.target.value)} required>
              <option value="" disabled>Pick a role</option>
              {form.roles.map((r) => <option key={r} value={r}>{sentence(r)}</option>)}
            </select>
          </label>
          <div className="field">
            <label htmlFor="enrol-photos" className="label">Photos of their face</label>
            <span className="hint" id="photos-hint">
              {min} to {max} clear photos, each of them alone, in good light. They stay on this device until you press Enrol.
            </span>
            {photos.length > 0 && (
              <ul className="list photo-picks" aria-label="Chosen photos">
                {photos.map((p, i) => (
                  <li key={p.url} className="photo-pick">
                    <img src={p.url} alt={`Photo ${i + 1}`} />
                    <button type="button" onClick={() => drop(i)} aria-label={`Take out photo ${i + 1}`}>&times;</button>
                  </li>
                ))}
              </ul>
            )}
            <div className="file-pick">
              {photos.length < max && (
                <>
                  <input id="enrol-photos" ref={input} className="sr-only" type="file" accept="image/*" multiple
                    aria-describedby="photos-hint" onChange={(e) => add(e.target.files)} />
                  <label htmlFor="enrol-photos" className="btn btn-sm">{photos.length ? "Add more photos" : "Choose photos"}</label>
                </>
              )}
              <span className="hint" aria-live="polite">{photos.length ? `${photos.length} of ${max} chosen` : ""}</span>
            </div>
          </div>
          <div className="stack" style={{ gap: 10 }}>
            <p className="consent-text" id="consent-text">{form.consent_text.replace(/^This person/, who)}</p>
            <label className="check">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} aria-describedby="consent-text" />
              <span>{who === "This person" ? "They agreed" : `${who} agreed`} to this, and it is true.</span>
            </label>
          </div>
          <Problem>{error}</Problem>
          <div>
            <button className="btn btn-primary" disabled={busy || !consent || photos.length < min || !name.trim() || !role}>
              {busy ? "Enrolling" : "Enrol"}
            </button>
          </div>
        </>
      )}
    </form>
  );
}

export default function People() {
  useTitle("People");
  const admin = useCan("admin");
  const staff = useLoad(() => api.staff(), []);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<StaffMember | null>(null);
  const [busy, setBusy] = useState(false);
  const confirm = useDialog();
  const data = staff.data;
  const list = data?.staff ?? [];

  async function remove() {
    if (!removing) return;
    setBusy(true);
    setError(null);
    try {
      const gone = await api.removeStaff(removing.id);
      const off = gone.disabled_watches ?? [];
      setDone(`${removing.name} is removed and will not be recognised again. Their past days stay in the staff register.`
        + (off.length ? ` These watches were about them and are now off: ${off.join(", ")}.` : ""));
      staff.reload();
      confirm.close();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page>
      <PageHead title="People" sub={<span>Staff who agreed to be recognised by face, for the staff register. Everyone else stays unknown.</span>} />
      {done && <p className="notice" role="status">{done}</p>}
      <div className="people-grid">
        <section className="section" aria-labelledby="enrolled-title">
          <div className="section-head">
            <h2 id="enrolled-title">Enrolled staff</h2>
            {data && <span className="small muted">{data.limit != null ? `${data.count} of ${data.limit} on your plan` : `${data.count} enrolled`}</span>}
          </div>
          <Problem>{staff.error}</Problem>
          {staff.loading && !data ? null : list.length === 0 ? (
            <Empty title="Nobody is enrolled yet">
              {admin ? "Enrol staff who agree, such as guards, maids and drivers. The staff register then records when they come and go." : "An admin can enrol staff who agree."}
            </Empty>
          ) : (
            <ul className="list card people-rows">
              {list.map((p) => (
                <li key={p.id}>
                  <div className="grow">
                    <p><strong>{p.name}</strong> <span className="muted small">{sentence(p.role)}</span></p>
                    {agreed(p.consent_at) && <p className="small muted">Agreed on {agreed(p.consent_at)}</p>}
                  </div>
                  {admin && (
                    <button className="btn btn-sm btn-quiet btn-danger" onClick={() => { setError(null); setRemoving(p); confirm.open(); }}>
                      Remove<span className="sr-only"> {p.name}</span>
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {list.length > 0 && <p className="small"><Link to="/app/logs/staff">See when they came and went</Link></p>}
        </section>

        {admin && data && (
          <EnrolForm form={data} onDone={(name) => {
            setDone(`${name} is enrolled. ${brand.name} now names them in the staff register.`);
            staff.reload();
          }} />
        )}
      </div>

      <Dialog dialog={confirm} title={`Remove ${removing?.name ?? ""}?`} id="remove-title" onClose={() => setRemoving(null)}>
        <p>{removing?.name} will not be recognised again, on any camera. Their face templates are deleted now.</p>
        <p className="small muted">Their past days stay in the staff register. To recognise them again, enrol them again with their consent.</p>
        <Problem>{error}</Problem>
        <div className="row">
          <button className="btn btn-primary" disabled={busy} onClick={remove}>{busy ? "Removing" : `Remove ${removing?.name ?? ""}`}</button>
          <button className="btn" onClick={confirm.close}>Keep</button>
        </div>
      </Dialog>
    </Page>
  );
}
