import { useRef, useState } from "react";
import { api } from "../api";
import type { Camera } from "../types";

function useDialog() {
  const ref = useRef<HTMLDialogElement>(null);
  return { ref, open: () => ref.current?.showModal(), close: () => ref.current?.close() };
}

// "Watch for this person again": a lookout built from one sighting's clothing, ending after 24 hours.
export function WatchAgainButton({ eventId, cameras, defaultCamera, label = "Watch for this person again" }: {
  eventId: string | null; cameras: Camera[]; defaultCamera?: string; label?: string;
}) {
  const d = useDialog();
  const live = cameras.filter((c) => c.kind !== "file");
  const [chosen, setChosen] = useState<string[]>(() => live.map((c) => c.id));
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function start(e: React.FormEvent) {
    e.preventDefault();
    if (!eventId) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.lookout({ camera_ids: chosen, event_id: eventId });
      setDone(`Watching ${r.watch_ids.length === 1 ? "1 camera" : `${r.watch_ids.length} cameras`} for this person for the next 24 hours.`);
      d.close();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!eventId) return null;
  if (done) return <p className="small notice notice-good" role="status">{done}</p>;
  const targets = live.length ? live : cameras.filter((c) => c.id === defaultCamera);
  return (
    <>
      <button type="button" className="btn btn-sm" onClick={() => { setChosen(targets.map((c) => c.id)); d.open(); }}>{label}</button>
      <dialog ref={d.ref} className="sheet" aria-labelledby={`wa-${eventId}`}>
        <form className="stack" onSubmit={start}>
          <h2 id={`wa-${eventId}`} className="h2">Watch for this person again</h2>
          <p className="small">Watchful looks for someone dressed the same way and tells you when they show up. This uses clothing, not faces, and ends on its own after 24 hours.</p>
          <fieldset className="fieldset stack">
            <legend className="label">On these cameras</legend>
            {targets.map((c) => (
              <label key={c.id} className="check small">
                <input type="checkbox" checked={chosen.includes(c.id)}
                  onChange={(e) => setChosen(e.target.checked ? [...chosen, c.id] : chosen.filter((x) => x !== c.id))} />
                {c.name}
              </label>
            ))}
          </fieldset>
          {error && <p className="notice notice-error" role="alert">{error}</p>}
          <div className="row">
            <button className="btn btn-primary" disabled={busy || chosen.length === 0}>{busy ? "Starting" : "Start watching"}</button>
            <button type="button" className="btn" onClick={d.close}>Cancel</button>
          </div>
        </form>
      </dialog>
    </>
  );
}

export function EnrolFromSighting({ eventId, onDone }: { eventId: string | null; onDone?: () => void }) {
  const d = useDialog();
  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function enrol(e: React.FormEvent) {
    e.preventDefault();
    if (!eventId) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("name", name.trim());
    form.append("consent", String(consent));
    form.append("event_id", eventId);
    try {
      await api.enrol(form);
      setDone(`${name.trim()} is enrolled. Watchful will recognise them from now on.`);
      d.close();
      onDone?.();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!eventId) return null;
  if (done) return <p className="small notice notice-good" role="status">{done}</p>;
  return (
    <>
      <button type="button" className="btn btn-sm btn-quiet" onClick={d.open}>Name this person</button>
      <dialog ref={d.ref} className="sheet" aria-labelledby={`en-${eventId}`}>
        <form className="stack" onSubmit={enrol}>
          <h2 id={`en-${eventId}`} className="h2">Name this person</h2>
          <p className="small">Watchful will recognise their face on your cameras and use this name in answers. Only do this with their consent. You can remove them any time under People.</p>
          <label className="field"><span>Name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} placeholder="Sana" />
          </label>
          <label className="check small">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} required />
            <span>This person has agreed to be recognised on these cameras.</span>
          </label>
          {error && <p className="notice notice-error" role="alert">{error === "No face found in that picture." ? "No face is clear enough in this picture. Pick a sighting where the face is visible, or enrol them from a photo under People." : error}</p>}
          <div className="row">
            <button className="btn btn-primary" disabled={busy || !consent || !name.trim()}>{busy ? "Enrolling" : "Enrol"}</button>
            <button type="button" className="btn" onClick={d.close}>Cancel</button>
          </div>
        </form>
      </dialog>
    </>
  );
}
