import { useMemo } from "react";
import { Link } from "react-router-dom";
import { api, frameUrl } from "../api";
import { AddCamera } from "../components/AddCamera";
import { AlertSummary } from "../components/AlertSummary";
import { cameraState, KIND_WORDS } from "../components/Tags";
import { Thumb } from "../components/Thumb";
import { clockDuration, scanLine } from "../format";
import { useLoad, useTitle } from "../hooks";
import type { Camera } from "../types";

function Tile({ cam }: { cam: Camera }) {
  const st = cameraState(cam);
  const live = cam.kind !== "file" && cam.status.running;
  const still = cam.kind === "webcam" ? null : frameUrl(cam.id);
  return (
    <li className="tile">
      <Link to={`/cameras/${cam.id}`} className="tile-link" aria-label={`Open ${cam.name}`}>
        <div className="tile-pic">
          <Thumb src={still} alt="" fill label={cam.kind === "webcam" ? "Opens your webcam" : "No picture yet"} />
        </div>
      </Link>
      <div className="tile-meta">
        <h3 className="h3"><Link to={`/cameras/${cam.id}`}>{cam.name}</Link></h3>
        <div className="tile-facts small">
          <span className="row" style={{ gap: 6 }}><span className={`live-dot ${st.tone}`} aria-hidden="true" />{st.label}</span>
          {live && cam.status.fps > 0 && <span className="num">{cam.status.fps.toFixed(1)} fps</span>}
          {cam.last_scan && <span className="num">{clockDuration(cam.last_scan.duration_s)} scanned in {cam.last_scan.wall_s.toFixed(1)} s</span>}
          <span className="muted">{KIND_WORDS[cam.kind]}</span>
          <span className="muted">{cam.zones.length === 1 ? "1 zone" : `${cam.zones.length} zones`}</span>
        </div>
      </div>
    </li>
  );
}

export default function Cameras() {
  useTitle("Cameras");
  const cameras = useLoad(() => api.cameras(), [], 5000);
  const alerts = useLoad(() => api.alerts(), [], 10000);
  const metrics = useLoad(() => api.metrics(), [], 30000);
  const names = useMemo(() => Object.fromEntries((cameras.data ?? []).map((c) => [c.id, c.name])), [cameras.data]);
  const scans = metrics.data?.scans ?? [];
  const latestScan = scans.at(-1) ?? (cameras.data ?? []).filter((c) => c.last_scan).at(-1)?.last_scan;
  const list = cameras.data ?? [];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Cameras</h1>
          <p className="lede">Open a camera to see it live, draw zones and tell Watchful what to look out for.</p>
        </div>
      </div>

      <div className="home-grid">
        <div className="stack" style={{ gap: 28 }}>
          {cameras.error && <p className="notice notice-error" role="alert">{cameras.error}</p>}
          {cameras.loading && !cameras.data ? <p className="muted">Loading cameras</p> : list.length === 0 ? (
            <div className="empty">
              <p>No cameras yet.</p>
              <p className="small">Add one below: upload a short video to see a scan, or use this browser's webcam.</p>
            </div>
          ) : (
            <ul className="list wall" aria-label="Cameras">
              {list.map((c) => <Tile key={c.id} cam={c} />)}
            </ul>
          )}
          <AddCamera onAdded={() => { cameras.reload(); metrics.reload(); }} />
        </div>

        <aside className="stack side" style={{ gap: 28 }}>
          <section className="section" aria-labelledby="latest-title">
            <div className="section-head">
              <h2 id="latest-title">Latest alerts</h2>
              <Link to="/alerts" className="small">All alerts</Link>
            </div>
            {(alerts.data ?? []).length === 0 ? (
              <div className="empty small"><p>Nothing needs you right now.</p><p>Alerts appear here as soon as a watch is triggered.</p></div>
            ) : (
              <div className="alert-list">
                {(alerts.data ?? []).slice(0, 5).map((a) => <AlertSummary key={a.id} alert={a} camera={names[a.camera_id]} />)}
              </div>
            )}
          </section>

          <section className="section speed" aria-labelledby="speed-title">
            <h2 id="speed-title">Scan speed</h2>
            {latestScan ? (
              <>
                <p className="speed-figure num">{latestScan.x_realtime >= 10 ? Math.round(latestScan.x_realtime) : latestScan.x_realtime.toFixed(1)}x <span>real time</span></p>
                <p className="small">{scanLine(latestScan)}.</p>
                <p className="xsmall muted">OpenCV splits each upload into parallel chunks and only runs detection where something moves. <Link to="/numbers">See the numbers</Link></p>
              </>
            ) : (
              <p className="small muted">Upload a video to see how fast OpenCV scans it. Every scan reports its own speed here.</p>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
