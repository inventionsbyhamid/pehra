import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, uploadVideo } from "../api";
import { tabKeys } from "../hooks";
import { ScanProgress } from "./ScanProgress";

type Mode = "upload" | "stream" | "webcam";
const MAX_MB = 200;

export function AddCamera({ onAdded }: { onAdded: () => void }) {
  const [mode, setMode] = useState<Mode>("upload");
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [sent, setSent] = useState<number | null>(null);
  const [scan, setScan] = useState<{ camera_id: string; scan_id: string } | null>(null);
  const abort = useRef<AbortController | null>(null);

  const tabs: { id: Mode; label: string }[] = [
    { id: "upload", label: "Upload a video" },
    { id: "stream", label: "Stream address" },
    { id: "webcam", label: "This browser's webcam" },
  ];

  function pick(m: Mode) {
    setMode(m);
    setError(null);
  }

  async function addStream(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const cam = await api.addCamera(name.trim() || "Stream camera", "stream", url.trim());
      onAdded();
      navigate(`/cameras/${cam.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function addWebcam(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const cam = await api.addCamera(name.trim() || "Webcam", "webcam");
      onAdded();
      navigate(`/cameras/${cam.id}?webcam=start`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    if (file.size > MAX_MB * 1024 * 1024) {
      setError(`That file is ${Math.round(file.size / 1048576)} MB. Videos can be up to ${MAX_MB} MB; trim it and try again.`);
      return;
    }
    setError(null);
    setScan(null);
    setSent(0);
    abort.current = new AbortController();
    try {
      const r = await uploadVideo(file, name.trim() || file.name.replace(/\.[^.]+$/, ""), setSent, abort.current.signal);
      setScan(r);
      onAdded();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSent(null);
    }
  }

  const uploading = sent !== null;

  return (
    <section className="panel add-camera" aria-labelledby="add-title">
      <div className="stack" style={{ gap: 4 }}>
        <h2 id="add-title" className="h2">Add a camera</h2>
        <p className="muted small">Watch a live stream, this browser's webcam, or check a recording in seconds.</p>
      </div>
      <div className="segmented" role="tablist" aria-label="How to add a camera" onKeyDown={tabKeys(tabs.map((t) => t.id), mode, pick)}>
        {tabs.map((t) => (
          <button key={t.id} role="tab" type="button" aria-selected={mode === t.id} aria-controls={`add-${t.id}`} tabIndex={mode === t.id ? 0 : -1}
            onClick={() => pick(t.id)}>{t.label}</button>
        ))}
      </div>

      {mode === "upload" && (
        <form id="add-upload" role="tabpanel" className="stack" onSubmit={upload}>
          <label className="field">
            <span>Video file</span>
            <input type="file" accept="video/*,.mp4,.mov,.avi,.mkv,.webm" disabled={uploading}
              onChange={(e) => { setFile(e.target.files?.[0] ?? null); setScan(null); setError(null); }} />
            <span className="hint">MP4 or similar, up to {MAX_MB} MB and 10 minutes. Uploads are deleted after 7 days.</span>
          </label>
          <label className="field">
            <span>Name <span className="muted">(optional)</span></span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Kitchen, Tuesday morning" disabled={uploading} />
          </label>
          <div className="row">
            <button className="btn btn-primary" disabled={!file || uploading}>Upload and scan</button>
            {uploading && <button type="button" className="btn" onClick={() => abort.current?.abort()}>Cancel upload</button>}
          </div>
          {uploading && (
            <div className="stack" style={{ gap: 6 }} aria-live="polite">
              <div className="spread small"><span>Uploading</span><span className="num muted">{Math.round(sent! * 100)}%</span></div>
              <div className="progress" role="progressbar" aria-label="Upload progress" aria-valuemin={0} aria-valuemax={100}
                aria-valuenow={Math.round(sent! * 100)}><div style={{ width: `${sent! * 100}%` }} /></div>
            </div>
          )}
          {scan && <ScanProgress scanId={scan.scan_id} cameraId={scan.camera_id} onDone={() => onAdded()} />}
        </form>
      )}

      {mode === "stream" && (
        <form id="add-stream" role="tabpanel" className="stack" onSubmit={addStream}>
          <label className="field">
            <span>Stream address</span>
            <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} required inputMode="url"
              placeholder="rtsp://192.168.1.20:554/stream1" />
            <span className="hint">An RTSP or HTTP address from an IP camera, or from a phone running an IP camera app.</span>
          </label>
          <label className="field">
            <span>Name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Front gate" />
          </label>
          <div><button className="btn btn-primary" disabled={busy || !url.trim()}>{busy ? "Adding" : "Add stream"}</button></div>
        </form>
      )}

      {mode === "webcam" && (
        <form id="add-webcam" role="tabpanel" className="stack" onSubmit={addWebcam}>
          <p className="small">Your browser sends its camera to Watchful about five times a second while the camera page is open. Nothing is sent once you close it.</p>
          <label className="field">
            <span>Name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Desk webcam" />
          </label>
          <div><button className="btn btn-primary" disabled={busy}>{busy ? "Adding" : "Add webcam"}</button></div>
        </form>
      )}

      {error && <p className="notice notice-error" role="alert">{error}</p>}
    </section>
  );
}
