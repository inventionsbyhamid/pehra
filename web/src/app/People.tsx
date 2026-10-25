import { useRef, useState } from "react";
import { api } from "../api";
import { Thumb } from "../components/Thumb";
import { initials } from "../format";
import { useLoad, useTitle } from "../hooks";
import brand from "../site/brand";
import type { Person } from "../types";
import { useCan } from "./session";
import { Dialog, useDialog } from "./ui/Dialog";
import { Empty, Page, PageHead, Problem } from "./ui/Page";

const NO_FACE = "No face was found in that photo. Use a clear photo of the face, looking at the camera, in good light.";

export default function People() {
  useTitle("People");
  const admin = useCan("admin");
  const people = useLoad(() => api.people(), []);
  const [name, setName] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [adult, setAdult] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Person | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const confirm = useDialog();

  function choose(f: File | null) {
    setPhoto(f);
    if (preview) URL.revokeObjectURL(preview);
    setPreview(f ? URL.createObjectURL(f) : null);
  }

  async function enrol(e: React.FormEvent) {
    e.preventDefault();
    if (!photo || !consent || !adult || !name.trim()) return;
    setBusy(true);
    setError(null);
    setDone(null);
    const form = new FormData();
    form.append("name", name.trim());
    form.append("consent", "true");
    form.append("photo", photo);
    try {
      const r = await api.enrol(form);
      setDone(`${r.name} is enrolled. ${brand.name} uses this name when it recognises their face.`);
      setName("");
      choose(null);
      setConsent(false);
      setAdult(false);
      if (fileInput.current) fileInput.current.value = "";
      people.reload();
    } catch (err) {
      const msg = (err as Error).message;
      setError(msg === "No face found in that picture." ? NO_FACE : msg);
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    if (!removing) return;
    try {
      await api.forget(removing.person_id);
      setDone(`${removing.name} was removed. Their face data is deleted and they are no longer recognised.`);
      people.reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      confirm.close();
    }
  }

  const list = people.data ?? [];

  return (
    <Page>
      <PageHead title="People" sub={<span>Staff who agreed to be recognised by face. Everyone else stays unknown, and their faces are never kept.</span>} />
      {done && <p className="notice" role="status">{done}</p>}
      <div className="people-grid">
        <section className="section" aria-labelledby="enrolled-title">
          <div className="section-head">
            <h2 id="enrolled-title">Enrolled</h2>
            <span className="small muted">{list.length === 1 ? "1 person" : `${list.length} people`}</span>
          </div>
          <Problem>{people.error}</Problem>
          {people.loading && !people.data ? null : list.length === 0 ? (
            <Empty title="Nobody is enrolled">
              {admin ? "Enrol staff who agree, such as guards, maids and drivers, so alerts and logs use their names." : "An admin can enrol staff who agree."}
            </Empty>
          ) : (
            <ul className="list card people-rows">
              {list.map((p) => (
                <li key={p.person_id}>
                  <Thumb src={p.thumbnail_url} alt={`Enrolment photo of ${p.name}`} w={48} h={48} label={initials(p.name)} />
                  <div className="grow">
                    <p><strong>{p.name}</strong></p>
                    {p.consent_ts && (
                      <p className="small muted">Agreed on {new Date(p.consent_ts * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}</p>
                    )}
                  </div>
                  {admin && (
                    <button className="btn btn-sm btn-quiet btn-danger" onClick={() => { setRemoving(p); confirm.open(); }}>
                      Remove<span className="sr-only"> {p.name}</span>
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {admin && (
          <form className="card stack" onSubmit={enrol} aria-labelledby="enrol-title">
            <h2 id="enrol-title" className="h2">Enrol someone</h2>
            <label className="field"><span>Name</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Sunita, cook" maxLength={60} required />
            </label>
            <div className="field">
              <label htmlFor="enrol-photo" className="label">Photo of their face</label>
              <div className="row">
                {preview && <img src={preview} alt="The chosen photo" className="enrol-preview" />}
                <input id="enrol-photo" ref={fileInput} type="file" accept="image/*" onChange={(e) => choose(e.target.files?.[0] ?? null)} />
              </div>
              <span className="hint">One clear photo of the face, looking at the camera, in good light.</span>
            </div>
            <label className="check">
              <input type="checkbox" checked={adult} onChange={(e) => setAdult(e.target.checked)} />
              <span>This person is an adult member of staff.</span>
            </label>
            <label className="check">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>They agreed to be recognised on these cameras, and know they can ask to be removed.</span>
            </label>
            <Problem>{error}</Problem>
            <div>
              <button className="btn btn-primary" disabled={busy || !consent || !adult || !photo || !name.trim()}>{busy ? "Enrolling" : "Enrol"}</button>
            </div>
          </form>
        )}
      </div>

      <Dialog dialog={confirm} title={`Remove ${removing?.name ?? ""}?`} id="remove-title" onClose={() => setRemoving(null)}>
        <p className="small">Their face data is deleted and {brand.name} stops recognising them on every camera.</p>
        <div className="row">
          <button className="btn btn-primary" onClick={forget}>Remove</button>
          <button className="btn" onClick={confirm.close}>Keep</button>
        </div>
      </Dialog>
    </Page>
  );
}
