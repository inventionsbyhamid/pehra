import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { WatchAgainButton } from "../components/PersonActions";
import { Thumb } from "../components/Thumb";
import { dayLabel, plain, span } from "../format";
import { useLoad, useTitle } from "../hooks";
import type { Sighting } from "../types";

type Mode = "person" | "description" | "photo";

function strength(s: Sighting): string {
  if (s.method === "face") return s.score >= 0.5 ? "Strong face match" : "Face match";
  return "Matches the description";
}

function SightingEvent({ s }: { s: Sighting }) {
  // Sightings carry a track, not an event; the camera's timeline links the two.
  const tl = useLoad(() => api.timeline(s.camera_id), [s.camera_id]);
  const id = tl.data?.events.find((e) => e.track_id === s.track_id)?.id ?? null;
  const cameras = useLoad(() => api.cameras(), []);
  if (!id) return null;
  return <WatchAgainButton eventId={id} cameras={cameras.data ?? []} defaultCamera={s.camera_id} />;
}

export default function FindPerson() {
  useTitle("Find a person");
  const people = useLoad(() => api.people(), []);
  const cameras = useLoad(() => api.cameras(), []);
  const [mode, setMode] = useState<Mode>("description");
  const [personId, setPersonId] = useState("");
  const [description, setDescription] = useState("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [cameraId, setCameraId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<{ query: string; mode: Mode; list: Sighting[]; person_id?: string; description?: string } | null>(null);
  const [lookout, setLookout] = useState<string | null>(null);
  const names = useMemo(() => Object.fromEntries((cameras.data ?? []).map((c) => [c.id, c.name])), [cameras.data]);

  const ready = mode === "person" ? !!personId : mode === "description" ? !!description.trim() : !!photo && consent;

  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    setLookout(null);
    try {
      let list: Sighting[];
      let query: string;
      if (mode === "photo") {
        const form = new FormData();
        form.append("photo", photo!);
        form.append("consent", String(consent));
        if (cameraId) form.append("camera_id", cameraId);
        list = await api.searchPerson(form);
        query = "the person in your photo";
      } else if (mode === "person") {
        list = await api.searchPerson({ person_id: personId, camera_id: cameraId || undefined });
        query = people.data?.find((p) => p.person_id === personId)?.name ?? "that person";
      } else {
        list = await api.searchPerson({ description: description.trim(), camera_id: cameraId || undefined });
        query = `“${description.trim()}”`;
      }
      setResults({ query, mode, list, person_id: mode === "person" ? personId : undefined, description: mode === "description" ? description.trim() : undefined });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function keepLookout() {
    if (!results) return;
    const live = (cameras.data ?? []).filter((c) => c.kind !== "file").map((c) => c.id);
    if (!live.length) {
      setLookout("There are no live cameras to keep a lookout on. Add a stream, a webcam or a demo camera first.");
      return;
    }
    try {
      const r = await api.lookout({ camera_ids: live, person_id: results.person_id, description: results.description });
      setLookout(`Keeping a lookout on ${r.watch_ids.length === 1 ? "1 live camera" : `${r.watch_ids.length} live cameras`}. You will get a "possible match" alert with a picture.`);
    } catch (err) {
      setLookout((err as Error).message);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Find a person</h1>
          <p className="lede">Search what the cameras recorded for someone you have enrolled, someone in a photo, or a description of what they wore.</p>
        </div>
      </div>

      <div className="find-grid">
        <form className="panel stack" onSubmit={search} aria-labelledby="find-how">
          <h2 id="find-how" className="sr-only">Search</h2>
          <div className="segmented" role="tablist" aria-label="Search by">
            {([["description", "Description"], ["person", "Enrolled person"], ["photo", "Photo"]] as const).map(([m, label]) => (
              <button key={m} type="button" role="tab" aria-selected={mode === m} onClick={() => { setMode(m); setError(null); }}>{label}</button>
            ))}
          </div>

          {mode === "description" && (
            <label className="field"><span>What do they look like?</span>
              <input className="input" value={description} onChange={(e) => setDescription(e.target.value)}
                placeholder="a man in a red shirt, a woman in a green saree" />
              <span className="hint">Clothing works best. The AI double checks the closest matches.</span>
            </label>
          )}
          {mode === "person" && (
            (people.data ?? []).length === 0 ? (
              <p className="notice">Nobody is enrolled yet. <Link to="/people">Enrol someone</Link> with their consent to search by name.</p>
            ) : (
              <label className="field"><span>Person</span>
                <select className="select" value={personId} onChange={(e) => setPersonId(e.target.value)}>
                  <option value="">Choose a person</option>
                  {(people.data ?? []).map((p) => <option key={p.person_id} value={p.person_id}>{p.name}</option>)}
                </select>
              </label>
            )
          )}
          {mode === "photo" && (
            <>
              <label className="field"><span>Photo with their face</span>
                <input type="file" accept="image/*" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
                <span className="hint">The photo is used for this search only and is not kept. A photo can't start a lookout.</span>
              </label>
              <label className="check small">
                <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                <span>This person agreed to be searched for with this photo.</span>
              </label>
            </>
          )}

          <label className="field"><span>Cameras</span>
            <select className="select" value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
              <option value="">All cameras</option>
              {(cameras.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          {error && <p className="notice notice-error" role="alert">{plain(error)}</p>}
          <div className="row">
            <button className="btn btn-primary" disabled={busy || !ready}>{busy ? "Searching" : "Search footage"}</button>
            {busy && <span className="spinner" aria-hidden="true" />}
          </div>
        </form>

        <section className="section" aria-live="polite" aria-labelledby="results-title">
          <h2 id="results-title" className={results ? "" : "sr-only"}>Results</h2>
          {!results ? (
            <div className="empty">
              <p>Results show every sighting with the camera, times and a picture.</p>
              <p className="small">Faces are matched only against stored keyframes when you search, so nothing is kept about people who are not enrolled.</p>
            </div>
          ) : (
            <>
              <div className="spread">
                <p>{results.list.length === 0 ? `No sightings of ${results.query}.` : `${results.list.length} ${results.list.length === 1 ? "sighting" : "sightings"} of ${results.query}`}</p>
                {results.mode !== "photo" && <button className="btn btn-sm" onClick={keepLookout}>Keep a lookout</button>}
              </div>
              {lookout && <p className="notice notice-good small" role="status">{lookout}</p>}
              {results.mode === "photo" && results.list.length > 0 && (
                <p className="hint">To be told when this person shows up again, enrol them under People with their consent, or use Watch for this person again on a sighting.</p>
              )}
              <ul className="list sightings">
                {results.list.map((s) => (
                  <li key={`${s.camera_id}-${s.track_id}`} className="sighting">
                    <Thumb src={s.thumbnail_url} alt="Sighting" w={96} h={128} />
                    <div className="stack" style={{ gap: 4 }}>
                      <p className="h3">{names[s.camera_id] ?? "Camera"}</p>
                      <p className="small num">{dayLabel(s.start_ts) !== "Today" && `${dayLabel(s.start_ts)} `}{span(s.start_ts, s.end_ts)}</p>
                      <p className="xsmall muted">{strength(s)}, score <span className="num">{s.score.toFixed(2)}</span></p>
                      <div className="row" style={{ gap: 6 }}>
                        <Link className="btn btn-sm btn-quiet" to={`/cameras/${s.camera_id}/timeline`}>Timeline</Link>
                        <SightingEvent s={s} />
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
