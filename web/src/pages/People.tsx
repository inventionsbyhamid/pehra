import { useRef, useState } from "react";
import { api } from "../api";
import { Thumb } from "../components/Thumb";
import { initials } from "../format";
import { useLoad, useTitle } from "../hooks";
import type { Person } from "../types";

export default function People() {
  useTitle("People");
  const people = useLoad(() => api.people(), []);
  const [name, setName] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Person | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const confirm = useRef<HTMLDialogElement>(null);

  function choose(f: File | null) {
    setPhoto(f);
    if (preview) URL.revokeObjectURL(preview);
    setPreview(f ? URL.createObjectURL(f) : null);
  }

  async function enrol(e: React.FormEvent) {
    e.preventDefault();
    if (!photo || !consent || !name.trim()) return;
    setBusy(true);
    setError(null);
    setDone(null);
    const form = new FormData();
    form.append("name", name.trim());
    form.append("consent", "true");
    form.append("photo", photo);
    try {
      const r = await api.enrol(form);
      setDone(`${r.name} is enrolled. Watchful will use this name when it recognises their face.`);
      setName("");
      choose(null);
      setConsent(false);
      if (fileInput.current) fileInput.current.value = "";
      people.reload();
    } catch (err) {
      const msg = (err as Error).message;
      setError(msg === "No face found in that picture." ? "No face was found in that photo. Use a clear, front facing photo with good light." : msg);
    } finally {
      setBusy(false);
    }
  }

  async function forget() {
    if (!removing) return;
    try {
      await api.forget(removing.person_id);
      setDone(`${removing.name} was removed. Their face data is deleted and they will no longer be recognised.`);
      people.reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      confirm.current?.close();
      setRemoving(null);
    }
  }

  const list = people.data ?? [];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>People</h1>
          <p className="lede">Watchful recognises only the people enrolled here, and only with their consent. Everyone else stays unknown, and their faces are never stored.</p>
        </div>
      </div>

      <div className="people-grid">
        <form className="panel stack" onSubmit={enrol} aria-labelledby="enrol-title">
          <h2 id="enrol-title" className="h2">Enrol someone</h2>
          <label className="field"><span>Name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Sana" maxLength={60} required />
            <span className="hint">The name Watchful uses in alerts and answers, for example "Sana, the maid".</span>
          </label>
          <div className="field">
            <label htmlFor="enrol-photo" className="label">Photo</label>
            <div className="row">
              {preview && <img src={preview} alt="Chosen photo" className="enrol-preview" />}
              <input id="enrol-photo" ref={fileInput} type="file" accept="image/*" onChange={(e) => choose(e.target.files?.[0] ?? null)} />
            </div>
            <span className="hint">One clear face, looking at the camera. You can also name someone from a camera's timeline.</span>
          </div>
          <label className="check">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>This person has agreed to be recognised on these cameras, and knows they can ask to be removed.</span>
          </label>
          {error && <p className="notice notice-error" role="alert">{error}</p>}
          {done && <p className="notice notice-good" role="status">{done}</p>}
          <div>
            <button className="btn btn-primary" disabled={busy || !consent || !photo || !name.trim()}>{busy ? "Enrolling" : "Enrol"}</button>
          </div>
        </form>

        <section className="section" aria-labelledby="enrolled-title">
          <div className="section-head">
            <h2 id="enrolled-title">Enrolled</h2>
            <span className="small muted">{list.length === 1 ? "1 person" : `${list.length} people`}</span>
          </div>
          {list.length === 0 ? (
            <div className="empty"><p>Nobody is enrolled.</p><p className="small">Until someone is, Watchful tells people apart only by clothing and timing, without names.</p></div>
          ) : (
            <ul className="list divided enrolled">
              {list.map((p) => (
                <li key={p.person_id}>
                  <Thumb src={p.thumbnail_url} alt={`Enrolment photo of ${p.name}`} w={56} h={56} label={initials(p.name)} />
                  <div className="grow">
                    <p className="h3">{p.name}</p>
                    {p.consent_ts && <p className="xsmall muted">Consent given {new Date(p.consent_ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}</p>}
                  </div>
                  <button className="btn btn-sm btn-danger" onClick={() => { setRemoving(p); confirm.current?.showModal(); }}>
                    Remove<span className="sr-only"> {p.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <dialog ref={confirm} className="sheet" aria-labelledby="remove-title" onClose={() => setRemoving(null)}>
        <div className="stack">
          <h2 id="remove-title" className="h2">Remove {removing?.name}?</h2>
          <p className="small">Their face data is deleted and Watchful stops recognising them on every camera.</p>
          <div className="row">
            <button className="btn btn-primary" onClick={forget}>Remove</button>
            <button className="btn" onClick={() => confirm.current?.close()}>Keep</button>
          </div>
        </div>
      </dialog>
    </div>
  );
}
