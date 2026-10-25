import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { DeviceCamera, type DeviceInfo, type DeviceState } from "./device";
import "./cam.css";

// The page a device link opens (/cam/{token}): this laptop or phone becomes one of the society's cameras.

// "Pool" reads "the Pool camera"; a name that already says camera stays as it is
export function cameraPhrase(name: string) {
  const n = name.trim();
  return /\bcam(era)?$/i.test(n) ? n : `${n} camera`;
}

const GONE = "This camera link no longer works. Ask your society admin for a new one.";

const STATUS: Record<string, string> = {
  live: "Sending to the control room.",
  reconnecting: "Lost the connection. Trying again.",
  taken: "Another device is now sending this camera, so this one stopped.",
  off: "This camera is turned off in the control room. Start again once an admin turns it back on.",
  gone: GONE,
};

export default function CamPage() {
  const { token = "" } = useParams();
  const [info, setInfo] = useState<DeviceInfo | null>(null);
  const [missing, setMissing] = useState<string | null>(null);
  const [state, setState] = useState<DeviceState>({ phase: "ready", sent: 0, covered: 0, blurReady: false });
  const [qr, setQr] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const device = useRef<DeviceCamera | null>(null);

  useEffect(() => {
    fetch(`/cam/${encodeURIComponent(token)}/info`, { cache: "no-store" })
      .then(async (r) => {
        if (r.ok) return setInfo(await r.json());
        setMissing(r.status === 429 ? "Too many tries from this network. Wait a minute, then reload the page." : GONE);
      })
      .catch(() => setMissing("The control room can't be reached. Check the connection, then reload the page."));
    return () => device.current?.stop();
  }, [token]);

  useEffect(() => {
    if (info) document.title = cameraPhrase(info.camera);
  }, [info]);

  function start() {
    if (!info || !canvas.current) return;
    device.current?.stop();
    device.current = new DeviceCamera(token, info, canvas.current, setState);
    void device.current.start();
  }

  function stop() {
    device.current?.stop();
    device.current = null;
    setState((s) => ({ ...s, phase: "ready" }));
  }

  if (missing) {
    return (
      <main className="cam">
        <p className="notice notice-error" role="alert">{missing}</p>
      </main>
    );
  }
  if (!info) return <main className="cam" aria-busy="true" />;

  const phase = state.phase;
  const running = phase === "starting" || phase === "live" || phase === "reconnecting";
  const shown = running || phase === "taken" || phase === "off";
  const status = phase === "starting"
    ? (info.blur && !state.blurReady ? "Getting face blur ready. The first time takes a few seconds." : "Starting the camera.")
    : phase === "error" ? state.problem : STATUS[phase];

  return (
    <main className="cam">
      <header className="cam-head">
        <p className="cam-society">{info.society}</p>
        <h1>This device is now the {cameraPhrase(info.camera)}</h1>
      </header>

      <div className="notice cam-notice" role="note">
        <p>
          {info.society} records this camera for everyone's safety.{" "}
          {info.blur
            ? "Faces are blurred on this device before any picture is sent."
            : "Faces are not blurred here, because this camera keeps the staff register. Only staff who agreed are recognised."}{" "}
          Recordings are kept for {info.retention_days} days, then deleted. Tell people near this camera that it records.
        </p>
      </div>

      <div className="stage cam-stage" hidden={!shown}>
        <canvas ref={canvas} role="img" data-sent={state.sent}
          aria-label={info.blur ? "What this camera sends, with faces covered" : "What this camera sends"}
          data-covered={state.covered} />
      </div>

      {status && (
        <p className={`cam-status${phase === "error" || phase === "gone" ? " cam-status-bad" : ""}`} role="status">
          {phase === "live" && <span className="live-dot" aria-hidden="true" />} {status}
        </p>
      )}

      {phase === "gone" ? null : running ? (
        <button className="btn cam-stop" onClick={stop}>Stop the camera</button>
      ) : (
        <button className="btn btn-primary cam-start" onClick={start} disabled={!info.enabled}>
          Start the camera
        </button>
      )}
      {!info.enabled && phase === "ready" && <p className="small muted">This camera is turned off in the control room.</p>}

      {phase !== "gone" && (
        <p className="small muted">Keep this page open with the screen on. Only one device sends this camera at a time.</p>
      )}

      {phase !== "gone" && <div className="cam-phone">
        <button className="btn btn-quiet btn-sm" onClick={() => setQr(!qr)} aria-expanded={qr}>
          {qr ? "Hide the code" : "Use a phone instead"}
        </button>
        {qr && (
          <div className="cam-qr">
            <img src={`/cam/${encodeURIComponent(token)}/qr.svg`} alt="A code that opens this page on a phone" />
            <p className="small muted">Scan it with the phone's camera. The phone then takes over from this device.</p>
          </div>
        )}
      </div>}
    </main>
  );
}
