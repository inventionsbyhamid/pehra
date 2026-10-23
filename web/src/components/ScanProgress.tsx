import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { plural, scanLine } from "../format";
import type { Scan } from "../types";

export function rememberScan(cameraId: string, scan: Scan) {
  try {
    localStorage.setItem(`watchful.scan.${cameraId}`, JSON.stringify(scan.stats));
  } catch {
    // storage is optional
  }
}

export function lastScan(cameraId: string): Scan["stats"] {
  try {
    return JSON.parse(localStorage.getItem(`watchful.scan.${cameraId}`) ?? "null");
  } catch {
    return null;
  }
}

export function ScanProgress({ scanId, cameraId, onDone, showLinks = true }: {
  scanId: string; cameraId: string; onDone?: (s: Scan) => void; showLinks?: boolean;
}) {
  const [scan, setScan] = useState<Scan | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    let timer = 0;
    const tick = async () => {
      try {
        const s = await api.scan(scanId);
        if (stop) return;
        setScan(s);
        if (s.stats) rememberScan(cameraId, s);
        if (s.state === "done" || s.state === "failed") {
          onDone?.(s);
          return;
        }
      } catch (e) {
        if (stop) return;
        setError((e as Error).message);
        return;
      }
      timer = window.setTimeout(tick, 400);
    };
    tick();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanId]);

  if (error) return <p className="notice notice-error" role="alert">{error}</p>;
  if (!scan) return <div className="row small"><span className="spinner" aria-hidden="true" />Starting the scan</div>;

  const pct = Math.round((scan.state === "scanning" ? scan.progress : 1) * 100);
  return (
    <div className="scan" aria-live="polite">
      {scan.state === "failed" ? (
        <p className="notice notice-error" role="alert">
          The scan stopped: {scan.error ?? "an unknown problem"}. Try Scan again, or upload the video once more.
        </p>
      ) : (
        <>
          <div className="spread small">
            <span>
              {scan.state === "scanning" && "Scanning with OpenCV"}
              {scan.state === "checking" && "Scanned. The AI is checking the flagged moments"}
              {scan.state === "done" && "Done"}
            </span>
            <span className="num muted">{pct}%</span>
          </div>
          <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}
            aria-label="Scan progress"><div style={{ width: `${pct}%` }} /></div>
        </>
      )}
      {scan.stats && (
        <p className="scan-line" data-testid="scan-line">
          <strong className="num">{scanLine(scan.stats)}</strong>
          <span className="muted small"> on {plural(scan.stats.workers, "CPU worker")}, {scan.stats.frames_analyzed.toLocaleString()} frames analysed.</span>
        </p>
      )}
      {scan.state === "done" && (
        <div className="row small">
          <span>{scan.alert_ids.length ? plural(scan.alert_ids.length, "alert") + " raised." : "No alerts raised."}</span>
          {showLinks && <Link to={`/cameras/${cameraId}`}>Open the recording</Link>}
          {showLinks && <Link to={`/cameras/${cameraId}/timeline`}>See the timeline</Link>}
          {scan.alert_ids.length > 0 && <Link to="/alerts">See alerts</Link>}
        </div>
      )}
    </div>
  );
}
