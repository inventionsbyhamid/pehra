import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, frameUrl } from "../api";
import { AlertSummary } from "../components/AlertSummary";
import { LiveView, type LiveHandle, type LiveStatus } from "../components/LiveView";
import { ScanProgress } from "../components/ScanProgress";
import { cameraState, KIND_WORDS } from "../components/Tags";
import { useWebcam } from "../components/useWebcam";
import { WatchesPanel } from "../components/WatchesPanel";
import { ZoneEditor } from "../components/ZoneEditor";
import { count, scanLine } from "../format";
import { useLoad, useTitle } from "../hooks";
import type { Alert, Camera, Zone } from "../types";

function GateStats({ status }: { status: LiveStatus | null }) {
  const s = status?.stats;
  if (!s?.frames) return null;
  const skipped = s.frames_skipped ?? 0;
  const pct = Math.round((skipped / s.frames) * 100);
  return (
    <dl className="gate-stats">
      <div><dt>Frames analysed</dt><dd className="num">{count(s.frames)}</dd></div>
      <div><dt>Skipped by the motion gate</dt><dd className="num">{count(skipped)} <span className="muted">({pct}%)</span></dd></div>
      <div><dt>Detector runs</dt><dd className="num">{count(s.detector_runs)}</dd></div>
      {s.ms_per_frame != null && <div><dt>Per frame</dt><dd className="num">{s.ms_per_frame.toFixed(0)} ms</dd></div>}
    </dl>
  );
}

function Stage({ cam, status, setStatus, onAlert, liveRef, webcam }: {
  cam: Camera; status: LiveStatus | null; setStatus: (s: LiveStatus) => void; onAlert: (a: Alert) => void;
  liveRef: React.RefObject<LiveHandle>; webcam: ReturnType<typeof useWebcam>;
}) {
  if (cam.kind === "file") {
    return (
      <div className="stage">
        <img src={frameUrl(cam.id)} alt={`A frame from ${cam.name}`} />
      </div>
    );
  }
  if (cam.kind === "webcam") {
    // the browser shows its own camera at full frame rate; the server adds boxes from its analysis
    return (
      <LiveView ref={liveRef} cameraId={cam.id} zones={cam.zones} mode="overlay" video={webcam.video}
        videoActive={webcam.state === "on"} onStatus={setStatus} onAlert={onAlert}
        idle={webcam.state === "starting" ? "Starting your webcam." : "Start the webcam to see it here with what Watchful notices drawn on top."}>
        <video ref={webcam.video} className="stage-video" muted playsInline aria-hidden="true" />
      </LiveView>
    );
  }
  const idle = cam.kind === "demo" ? "Starting the demo camera. The first picture takes a few seconds."
    : status?.source === "reconnecting" ? "Can't reach this stream yet. Watchful keeps trying, waiting a little longer each time."
    : "Connecting to the stream.";
  return <LiveView ref={liveRef} cameraId={cam.id} zones={cam.zones} mode="frames" onStatus={setStatus} onAlert={onAlert} idle={idle} />;
}

export default function CameraView() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const camera = useLoad(() => api.camera(id), [id], 10000);
  const alerts = useLoad(() => api.alerts(id), [id], 20000);
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [fresh, setFresh] = useState<Alert[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [zoneError, setZoneError] = useState<string | null>(null);
  const [scanId, setScanId] = useState<string | null>(null);
  const [scanBusy, setScanBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const liveRef = useRef<LiveHandle>(null);
  const confirmRemove = useRef<HTMLDialogElement>(null);
  const [removing, setRemoving] = useState(false);
  const webcam = useWebcam(id);
  const cam = camera.data;
  useTitle(cam?.name ?? "Camera");

  useEffect(() => {
    if (cam?.kind === "webcam" && params.get("webcam") === "start") {
      webcam.setOn(true);
      params.delete("webcam");
      setParams(params, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cam?.kind]);

  const allAlerts = useMemo(() => {
    const seen = new Set<string>();
    return [...fresh, ...(alerts.data ?? [])].filter((a) => a.status !== "dismissed" && !seen.has(a.id) && seen.add(a.id));
  }, [fresh, alerts.data]);

  if (camera.loading && !cam) return <div className="page"><p className="muted">Loading camera</p></div>;
  if (!cam) {
    return (
      <div className="page">
        <p className="notice notice-error" role="alert">{camera.error ?? "There is no camera with this address. It may have been removed."}</p>
        <Link to="/">Back to cameras</Link>
      </div>
    );
  }

  const live: Camera = status
    ? { ...cam, status: { ...cam.status, running: status.source !== "stopped", source: status.source, fps: status.fps, loops: status.loops } }
    : cam;
  const st = cameraState(live);
  const last = cam.last_scan;

  function startEditing() {
    const snap = cam!.kind === "file" ? null : liveRef.current?.snapshot();
    setZoneError(null);
    setEditing(snap ?? frameUrl(cam!.id, String(Date.now())));
  }

  async function saveZones(zones: Zone[]) {
    setSaving(true);
    setZoneError(null);
    try {
      const updated = await api.patchCamera(cam!.id, { zones });
      camera.setData(updated);
      setEditing(null);
    } catch (e) {
      setZoneError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function rescan() {
    setActionError(null);
    try {
      setScanId((await api.rescan(cam!.id)).scan_id);
      setScanBusy(true);
    } catch (e) {
      setActionError((e as Error).message);
    }
  }

  async function remove() {
    setRemoving(true);
    setActionError(null);
    try {
      await api.deleteCamera(cam!.id);
      navigate("/", { replace: true });
    } catch (e) {
      setActionError((e as Error).message);
      confirmRemove.current?.close();
    } finally {
      setRemoving(false);
    }
  }

  async function setEnabled(enabled: boolean) {
    setActionError(null);
    try {
      camera.setData(await api.patchCamera(cam!.id, { enabled }));
    } catch (e) {
      setActionError((e as Error).message);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <Link to="/" className="small back">Cameras</Link>
          <h1>{cam.name}</h1>
          <div className="row small cam-facts">
            <span className="row" style={{ gap: 6 }}><span className={`live-dot ${st.tone}`} aria-hidden="true" />{st.label}</span>
            {cam.kind !== "file" && live.status.fps > 0 && <span className="num">{live.status.fps.toFixed(1)} frames a second</span>}
            <span className="muted">{KIND_WORDS[cam.kind]}</span>
          </div>
        </div>
        <div className="row">
          {cam.kind === "webcam" && (
            <button className={`btn ${webcam.on ? "" : "btn-primary"}`} onClick={() => webcam.setOn(!webcam.on)} aria-pressed={webcam.on}>
              {webcam.on ? "Stop webcam" : "Start webcam"}
            </button>
          )}
          {cam.kind === "file" && <button className="btn" onClick={rescan} disabled={scanBusy}>Scan again</button>}
          <button className="btn" onClick={() => (editing ? setEditing(null) : startEditing())} aria-pressed={!!editing}>
            {editing ? "Close zones" : cam.zones.length ? "Edit zones" : "Draw zones"}
          </button>
          <Link className="btn" to={`/cameras/${cam.id}/timeline`}>Timeline</Link>
          {cam.kind !== "file" && (
            <button className="btn btn-quiet" onClick={() => setEnabled(!cam.enabled)}>{cam.enabled ? "Pause camera" : "Resume camera"}</button>
          )}
          <button className="btn btn-quiet btn-danger" onClick={() => confirmRemove.current?.showModal()}>Remove camera</button>
        </div>
      </div>

      <dialog ref={confirmRemove} className="sheet" aria-labelledby="remove-cam-title">
        <div className="stack">
          <h2 id="remove-cam-title" className="h2">Remove {cam.name}?</h2>
          <p className="small">This stops the camera and deletes its watches, events, alerts and clips{cam.kind === "file" ? ", and the uploaded video" : ""}. It can't be undone.</p>
          <div className="row">
            <button className="btn btn-primary" onClick={remove} disabled={removing}>{removing ? "Removing" : "Remove camera"}</button>
            <button className="btn" onClick={() => confirmRemove.current?.close()}>Keep it</button>
          </div>
        </div>
      </dialog>

      {webcam.error && <p className="notice notice-error" role="alert">{webcam.error}</p>}
      {actionError && <p className="notice notice-error" role="alert">{actionError}</p>}

      <div className="camera-grid">
        <div className="stack" style={{ gap: 18 }}>
          {editing ? (
            <ZoneEditor background={editing} zones={cam.zones} saving={saving} error={zoneError}
              onSave={saveZones} onCancel={() => setEditing(null)} />
          ) : (
            <Stage cam={cam} status={status} setStatus={setStatus} onAlert={(a) => setFresh((f) => [a, ...f])} liveRef={liveRef} webcam={webcam} />
          )}
          {!editing && cam.kind !== "file" && <GateStats status={status} />}
          {!editing && cam.kind === "webcam" && webcam.state === "on" && (
            <p className="small muted">Sending your webcam at about 5 frames a second. {webcam.sent > 0 && <span className="num">{count(webcam.sent)} sent.</span>}</p>
          )}
          {cam.kind === "file" && (
            <div className="panel stack">
              {scanId ? (
                <ScanProgress scanId={scanId} cameraId={cam.id} showLinks={false} onDone={() => { setScanBusy(false); alerts.reload(); camera.reload(); }} />
              ) : last ? (
                <p className="scan-line"><strong className="num">{scanLine(last)}</strong></p>
              ) : (
                <p className="small muted">This recording was scanned when it was uploaded. Scan again after changing zones or watches.</p>
              )}
            </div>
          )}

          <section className="section" aria-labelledby="cam-alerts">
            <div className="section-head">
              <h2 id="cam-alerts">Alerts from this camera</h2>
              {allAlerts.length > 0 && <Link to="/alerts" className="small">All alerts</Link>}
            </div>
            {allAlerts.length === 0 ? (
              <p className="muted small">None yet. When a watch is triggered, the AI checks the moment and the alert shows up here.</p>
            ) : (
              <div className="alert-list" aria-live="polite">
                {allAlerts.slice(0, 6).map((a) => <AlertSummary key={a.id} alert={a} />)}
              </div>
            )}
          </section>
        </div>

        <aside className="side">
          <WatchesPanel camera={cam} />
        </aside>
      </div>
    </div>
  );
}
