import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { WatchAgainButton } from "../components/PersonActions";
import { Thumb } from "../components/Thumb";
import { dayLabel, plain, span, timeOf, when } from "../format";
import { useLoad, useTitle } from "../hooks";
import type { Camera, SearchQuery, SearchResponse, SearchResult } from "../types";

const EXAMPLES = ["a person in a red shirt", "someone carrying a box", "a car in the parking lot", "people without helmets"];

function whenWords(q: SearchQuery): string {
  if (q.since && q.until) return `between ${when(q.since)} and ${when(q.until)}`;
  if (q.since) return `since ${when(q.since)}`;
  if (q.until) return `before ${when(q.until)}`;
  return "at any time";
}

function QueryLine({ q, cameras, label }: { q: SearchQuery; cameras: Record<string, Camera>; label: string }) {
  const where = q.camera_id ? cameras[q.camera_id]?.name ?? "one camera" : "all cameras";
  return (
    <p className="sentence sentence-sm" data-testid="search-query">
      <span className="w">Looking for</span>
      <span className="slot"><b>{plain(q.looks_like) || label}</b><i>looks like</i></span>
      {q.not_like && (
        <>
          <span className="w">and not</span>
          <span className="slot"><b>{plain(q.not_like)}</b><i>not like</i></span>
        </>
      )}
      <span className="w">on</span>
      <span className="slot"><b>{where}</b><i>camera</i></span>
      {!!q.zones?.length && (
        <>
          <span className="w">in</span>
          <span className="slot"><b>{q.zones!.join(", ")}</b><i>where</i></span>
        </>
      )}
      <span className="slot"><b>{whenWords(q)}</b><i>when</i></span>
    </p>
  );
}

function ResultCard({ r, cameras, all }: { r: SearchResult; cameras: Record<string, Camera>; all: Camera[] }) {
  const cam = cameras[r.camera_id];
  const start = r.start_ts ?? r.ts;
  const person = r.kind === "object" && r.cls === "person";
  return (
    <li className={`result result-${r.kind}`}>
      <div className="result-pic">
        <Thumb src={r.thumbnail_url} alt={r.kind === "scene" ? `The view on ${cam?.name ?? "a camera"}` : `A ${r.cls ?? "match"}`} fill />
      </div>
      <div className="result-meta">
        <p className="small num">
          {dayLabel(start) !== "Today" && `${dayLabel(start)} `}{r.start_ts != null ? span(r.start_ts, r.end_ts) : timeOf(r.ts)}
        </p>
        <p className="xsmall muted">
          {cam?.name ?? "Camera"}{r.kind === "object" && r.cls ? `, ${r.cls}` : ""}
        </p>
        <div className="row" style={{ gap: 6 }}>
          {r.method === "face" && <span className="tag tag-accent">Face match</span>}
          {r.confirmed && <span className="tag tag-good">Checked by AI</span>}
        </div>
        <div className="row" style={{ gap: 4 }}>
          <Link className="btn btn-sm btn-quiet" to={`/cameras/${r.camera_id}/timeline`}>Timeline</Link>
          {person && r.event_id && <WatchAgainButton eventId={r.event_id} cameras={all} defaultCamera={r.camera_id} />}
        </div>
      </div>
    </li>
  );
}

export default function Find() {
  useTitle("Find");
  const cameras = useLoad(() => api.cameras(), []);
  const [text, setText] = useState("");
  const [cameraId, setCameraId] = useState("");
  const [usePhoto, setUsePhoto] = useState(false);
  const [photo, setPhoto] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [res, setRes] = useState<{ data: SearchResponse; text: string; label: string; photo: boolean } | null>(null);
  const [lookout, setLookout] = useState<string | null>(null);
  const camById = useMemo(() => Object.fromEntries((cameras.data ?? []).map((c) => [c.id, c])), [cameras.data]);

  async function run(body: { text: string } | null, label: string) {
    setBusy(true);
    setError(null);
    setLookout(null);
    try {
      let data: SearchResponse;
      if (body === null) {
        const form = new FormData();
        form.append("photo", photo!);
        form.append("consent", String(consent));
        if (text.trim()) form.append("text", text.trim());
        if (cameraId) form.append("camera_id", cameraId);
        data = await api.search(form);
      } else {
        data = await api.search({ ...body, camera_id: cameraId || undefined });
      }
      setRes({ data, text: body?.text ?? "", label, photo: body === null });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (usePhoto) {
      if (photo && consent) run(null, "the person in your photo");
    } else if (text.trim()) {
      run({ text: text.trim() }, text.trim());
    }
  }

  async function keepLookout() {
    if (!res?.text) return;
    const live = (cameras.data ?? []).filter((c) => c.kind !== "file").map((c) => c.id);
    if (!live.length) {
      setLookout("There are no live cameras to keep a lookout on. Add a stream, a webcam or a demo camera first.");
      return;
    }
    try {
      const r = await api.lookout({ camera_ids: live, description: res.text });
      setLookout(`Keeping a lookout on ${r.watch_ids.length === 1 ? "1 live camera" : `${r.watch_ids.length} live cameras`}. You will get a "possible match" alert with a picture.`);
    } catch (e) {
      setLookout((e as Error).message);
    }
  }

  const ready = usePhoto ? !!photo && consent : !!text.trim();
  const results = res?.data.results ?? [];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Find</h1>
          <p className="lede">Describe what you are looking for. Watchful searches pictures of everything the cameras saw, and the AI double checks the best matches.</p>
        </div>
      </div>

      <form className="panel find-box stack" onSubmit={submit}>
        <label className="field">
          <span>Find anything</span>
          <div className="find-input">
            <input className="input" value={text} onChange={(e) => setText(e.target.value)}
              placeholder={usePhoto ? "Optional: where or when, like yesterday at the gate" : "a person in a red shirt"} />
            <button className="btn btn-primary" disabled={busy || !ready}>{busy ? "Searching" : "Search"}</button>
          </div>
        </label>
        {!usePhoto && (
          <div className="row chips" role="group" aria-label="Examples">
            {EXAMPLES.map((x) => (
              <button key={x} type="button" className="btn btn-sm" disabled={busy}
                onClick={() => { setText(x); run({ text: x }, x); }}>{x}</button>
            ))}
          </div>
        )}
        <div className="row find-options">
          <select className="select select-sm" aria-label="Cameras" value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
            <option value="">All cameras</option>
            {(cameras.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <label className="check small"><input type="checkbox" checked={usePhoto} onChange={(e) => setUsePhoto(e.target.checked)} />Search with a photo</label>
        </div>
        {usePhoto && (
          <div className="stack photo-search">
            <label className="field"><span>Photo</span>
              <input type="file" accept="image/*" onChange={(e) => setPhoto(e.target.files?.[0] ?? null)} />
              <span className="hint">A photo with a face searches for that face. Otherwise Watchful looks for things that look like it. The photo is not kept.</span>
            </label>
            <label className="check small">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              <span>If this photo shows a person, they agreed to be searched for with it.</span>
            </label>
          </div>
        )}
        {error && <p className="notice notice-error" role="alert">{plain(error)}</p>}
      </form>

      <section className="section" aria-live="polite" aria-labelledby="results-title">
        <h2 id="results-title" className="sr-only">Results</h2>
        {!res ? (
          <div className="empty">
            <p>Results show each moment with the camera, the time and a picture, best match first.</p>
            <p className="small">Faces are compared only for enrolled people or a photo you add with consent. Nothing is kept about anyone else.</p>
          </div>
        ) : (
          <>
            <div className="spread">
              {res.data.query ? <QueryLine q={res.data.query} cameras={camById} label={res.label} /> : <span />}
              {!res.photo && res.text && results.length > 0 && (
                <button className="btn btn-sm" onClick={keepLookout}>Keep a lookout</button>
              )}
            </div>
            {lookout && <p className="notice notice-good small" role="status">{lookout}</p>}
            {res.data.problem && <p className="notice notice-warn" role="alert">{plain(res.data.problem)}</p>}
            {results.length === 0 ? (
              !res.data.problem && <div className="empty"><p>Nothing matched. Try fewer details, another camera, or a wider time.</p></div>
            ) : (
              <>
                <p className="small muted">{results.length === 1 ? "1 moment" : `${results.length} moments`}, best match first.</p>
                <ol className="list results" data-testid="search-results">
                  {results.map((r, i) => <ResultCard key={`${r.event_id ?? r.ts}-${i}`} r={r} cameras={camById} all={cameras.data ?? []} />)}
                </ol>
              </>
            )}
          </>
        )}
      </section>
    </div>
  );
}
