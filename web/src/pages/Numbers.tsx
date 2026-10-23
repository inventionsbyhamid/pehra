import { api } from "../api";
import { STATUS_WORDS, KIND_WORDS } from "../components/Tags";
import { clockDuration, count, times } from "../format";
import { useLoad, useTitle } from "../hooks";
import type { AlertStatus } from "../types";

const STATUS_ORDER: AlertStatus[] = ["alert", "awaiting_human", "unverified", "approved", "declined", "dismissed"];

export default function Numbers() {
  useTitle("Numbers");
  const m = useLoad(() => api.metrics(), [], 5000);
  const data = m.data;
  const totalAlerts = data ? Object.values(data.alerts).reduce((a, b) => a + (b ?? 0), 0) : 0;
  const maxAlerts = data ? Math.max(1, ...Object.values(data.alerts).map((v) => v ?? 0)) : 1;
  const scans = [...(data?.scans ?? [])].reverse();
  const maxX = Math.max(1, ...scans.map((s) => s.x_realtime));
  const used = data ? data.ai_calls_today / Math.max(1, data.ai_daily_limit) : 0;
  const frames = data?.cameras.reduce((a, c) => a + (c.status.stats.frames ?? 0), 0) ?? 0;
  const skipped = data?.cameras.reduce((a, c) => a + (c.status.stats.frames_skipped ?? 0), 0) ?? 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Numbers</h1>
          <p className="lede">What OpenCV and the AI are doing right now, measured as it runs. Updates every few seconds.</p>
        </div>
      </div>
      {m.error && <p className="notice notice-error" role="alert">{m.error}</p>}
      {!data ? <p className="muted">Loading numbers</p> : (
        <>
          <div className="figures">
            <div className="figure">
              <p className="figure-value num">{frames ? `${Math.round((skipped / frames) * 100)}%` : "None yet"}</p>
              <p className="small">of live frames skipped by the motion gate, so the detector only runs when something moves</p>
            </div>
            <div className="figure">
              <p className="figure-value num">{scans[0] ? `${times(scans[0].x_realtime)}` : "None yet"}</p>
              <p className="small">{scans[0] ? `real time on the last upload: ${clockDuration(scans[0].duration_s)} of video in ${scans[0].wall_s.toFixed(1)} s` : "real time on uploads, once a video has been scanned"}</p>
            </div>
            <div className="figure">
              <p className="figure-value num">{count(data.ai_calls_today)}</p>
              <p className="small">AI calls today, of a daily limit of {count(data.ai_daily_limit)}. The AI only sees moments OpenCV flags.</p>
              <div className="meter" role="meter" aria-label="AI calls used today" aria-valuemin={0} aria-valuemax={data.ai_daily_limit} aria-valuenow={data.ai_calls_today}>
                <div style={{ width: `${Math.min(100, used * 100)}%` }} className={used > 0.85 ? "is-high" : ""} />
              </div>
            </div>
          </div>

          <section className="section" aria-labelledby="per-camera">
            <h2 id="per-camera">Per camera</h2>
            <div className="table-wrap panel panel-flush">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Camera</th>
                    <th scope="col" className="r">Frames a second</th>
                    <th scope="col" className="r">Frames analysed</th>
                    <th scope="col" className="r">Skipped by motion gate</th>
                    <th scope="col" className="r">Detector runs</th>
                    <th scope="col" className="r">Pose runs</th>
                    <th scope="col" className="r">Face checks</th>
                    <th scope="col" className="r">Time per frame</th>
                  </tr>
                </thead>
                <tbody>
                  {data.cameras.map((c) => {
                    const s = c.status.stats;
                    const pct = s.frames ? Math.round(((s.frames_skipped ?? 0) / s.frames) * 100) : null;
                    return (
                      <tr key={c.id}>
                        <th scope="row" style={{ fontWeight: 600, textAlign: "left" }}>{c.name}<br /><span className="xsmall muted">{KIND_WORDS[c.kind]}{c.status.running ? "" : ", not running"}</span></th>
                        <td className="r">{c.status.running ? c.status.fps.toFixed(1) : ""}</td>
                        <td className="r">{s.frames != null ? count(s.frames) : ""}</td>
                        <td className="r">{s.frames != null ? <>{count(s.frames_skipped)}{pct != null && <span className="muted"> ({pct}%)</span>}</> : ""}</td>
                        <td className="r">{s.detector_runs != null ? count(s.detector_runs) : ""}</td>
                        <td className="r">{s.pose_runs != null ? count(s.pose_runs) : ""}</td>
                        <td className="r">{s.face_runs != null ? count(s.face_runs) : ""}</td>
                        <td className="r">{s.ms_per_frame != null ? `${s.ms_per_frame.toFixed(0)} ms` : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="hint">Counts start when a camera's analysis starts. Uploaded videos are counted under scans below.</p>
          </section>

          <div className="two-col">
            <section className="section" aria-labelledby="scan-speeds">
              <h2 id="scan-speeds">Recent scans</h2>
              {scans.length === 0 ? <div className="empty"><p>No uploads scanned since the server started.</p></div> : (
                <ul className="list bars">
                  {scans.map((s, i) => (
                    <li key={i}>
                      <div className="spread small">
                        <span className="num">{clockDuration(s.duration_s)} of video in {s.wall_s.toFixed(1)} s</span>
                        <strong className="num">{times(s.x_realtime)} real time</strong>
                      </div>
                      <div className="bar"><div style={{ width: `${(s.x_realtime / maxX) * 100}%` }} /></div>
                      <p className="xsmall muted num">{count(s.frames_analyzed)} frames, {count(s.detector_runs)} detector runs, {s.workers} workers</p>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="section" aria-labelledby="alert-counts">
              <h2 id="alert-counts">Alerts by outcome</h2>
              {totalAlerts === 0 ? <div className="empty"><p>No alerts yet.</p></div> : (
                <ul className="list bars">
                  {STATUS_ORDER.filter((k) => data.alerts[k]).map((k) => (
                    <li key={k}>
                      <div className="spread small"><span>{STATUS_WORDS[k]}</span><strong className="num">{data.alerts[k]}</strong></div>
                      <div className="bar"><div style={{ width: `${((data.alerts[k] ?? 0) / maxAlerts) * 100}%` }} /></div>
                    </li>
                  ))}
                </ul>
              )}
              {totalAlerts > 0 && (
                <p className="hint">The AI dismissed {count(data.alerts.dismissed ?? 0)} of {count(totalAlerts)} flagged moments before they reached you.</p>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
