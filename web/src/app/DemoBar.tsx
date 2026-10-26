import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, type DeviceLink } from "../api";
import { epoch } from "../format";
import { useLoad, useTitle } from "../hooks";
import brand from "../site/brand";
import SiteLayout from "../site/SiteLayout";
import { Copy } from "./ui/Copy";
import { Dialog, useDialog } from "./ui/Dialog";
import { Problem } from "./ui/Page";
import "./demo.css";

function left(iso: string | undefined): string {
  const s = Math.max(0, epoch(iso) - Date.now() / 1000);
  if (s >= 5400) return `${Math.round(s / 3600)} hours`;
  if (s >= 3600) return "an hour";
  return `${Math.max(1, Math.round(s / 60))} minutes`;
}

// A new demo from scratch: the server starts one and signs this browser in to it; a full load opens it.
async function startDemo(signOut = false): Promise<void> {
  const { next } = await api.demoLogin(signOut);
  window.location.assign(next);
}

type How = "device" | "phone";

// Add your camera: this laptop or phone, or a phone through the device link's QR code. Both use the app's own
// camera page and watch editor after.
function AddYourCamera({ dialog, onAdded }: { dialog: ReturnType<typeof useDialog>; onAdded: () => void }) {
  const navigate = useNavigate();
  const [how, setHow] = useState<How>("device");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phone, setPhone] = useState<{ id: string; link: DeviceLink } | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const cam = await api.addCamera({ name: how === "device" ? "My camera" : "My phone", kind: "webcam", purpose: "other" });
      onAdded();
      if (how === "device") {
        dialog.close();
        navigate(`/app/cameras/${cam.id}?webcam=start`);
        return;
      }
      setPhone({ id: cam.id, link: await api.deviceLink(cam.id) });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function go(to: string) {
    dialog.close();
    navigate(to);
  }

  return (
    <Dialog dialog={dialog} title="Add your camera" id="add-yours-title" onClose={() => { setPhone(null); setError(null); }}>
      {phone ? (
        <div className="stack" style={{ gap: 14 }}>
          <p>Scan this with the phone's camera. The page it opens sends the phone's pictures here while it stays open.</p>
          <img className="demo-qr" src={phone.link.qr_url} alt="QR code for the phone's camera page" width={220} height={220} />
          <Copy value={phone.link.url} label="The phone's camera page" />
          <p className="small muted">Faces are blurred on the phone before a picture leaves it.</p>
          <div className="row">
            <button type="button" className="btn btn-primary" onClick={() => go(`/app/watches/new?camera=${phone.id}`)}>Add a watch</button>
            <button type="button" className="btn" onClick={() => go(`/app/cameras/${phone.id}`)}>Open the camera</button>
            <button type="button" className="btn btn-quiet" onClick={dialog.close}>Done</button>
          </div>
        </div>
      ) : (
        <form className="stack" style={{ gap: 14 }} onSubmit={add}>
          <fieldset className="options">
            <legend className="sr-only">How it joins</legend>
            <label className="option">
              <input type="radio" name="demo-how" value="device" checked={how === "device"} onChange={() => setHow("device")} />
              <strong>This device</strong>
              <span>This laptop or phone becomes a camera while its page is open.</span>
            </label>
            <label className="option">
              <input type="radio" name="demo-how" value="phone" checked={how === "phone"} onChange={() => setHow("phone")} />
              <strong>A phone</strong>
              <span>Scan a QR code with your phone and it becomes a camera.</span>
            </label>
          </fieldset>
          <p className="small muted">Then type what {brand.name} should watch for, see what it will and won't watch, save it and see it fire.</p>
          <Problem>{error}</Problem>
          <div className="row">
            <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Adding" : "Add it"}</button>
            <button type="button" className="btn btn-quiet" onClick={dialog.close}>Cancel</button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

// The short bar over a demo society: how long it lasts, adding your own camera, where the pictures come from.
export function DemoBar() {
  const info = useLoad(() => api.demo(), [], 60_000);
  const dialog = useDialog();
  const [about, setAbout] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const d = info.data;
  if (!d) return null;

  if (d.ended) {
    return (
      <div className="demo-bar" role="region" aria-label="Demo">
        <div className="wrap demo-bar-row">
          <p><strong>This demo has ended.</strong> Its cameras and pictures are deleted within a day.</p>
          <button type="button" className="btn btn-primary btn-sm"
            onClick={() => startDemo().catch((e: Error) => setError(e.message))}>Start a new demo</button>
        </div>
        {error && <div className="wrap"><Problem>{error}</Problem></div>}
      </div>
    );
  }

  const full = d.own_cameras >= d.own_cameras_allowed;
  return (
    <div className="demo-bar" role="region" aria-label="Demo">
      <div className="wrap demo-bar-row">
        <p>
          <strong>Demo society.</strong> Yours for {left(d.expires_at)}. Nothing here goes to Telegram.{" "}
          <button type="button" className="linkish" aria-expanded={about} onClick={() => setAbout(!about)}>About the cameras</button>
        </p>
        <div className="row">
          {!full && <button type="button" className="btn btn-primary btn-sm" onClick={dialog.open}>Add your camera</button>}
          <a className="btn btn-sm" href="/login">Start your own</a>
        </div>
      </div>
      {about && (
        <div className="wrap demo-about">
          <ul>
            {d.clips.map((c) => <li key={c.camera}><strong>{c.camera}</strong> replays a clip in a loop, as if live. {c.source}</li>)}
            <li>Your own camera is live. Its pictures are deleted with this demo.</li>
          </ul>
        </div>
      )}
      <AddYourCamera dialog={dialog} onAdded={() => info.reload()} />
    </div>
  );
}

// /demo: Try the demo lands here; it starts a demo society and opens its control room.
export function DemoStart() {
  useTitle("Try the demo");
  const [problem, setProblem] = useState<string | null>(null);
  const [member, setMember] = useState(false);
  const asked = useRef(false);

  useEffect(() => {
    if (asked.current) return;
    asked.current = true;
    startDemo().catch((e: Error) => {
      if (e instanceof ApiError && e.status === 409) setMember(true);
      else setProblem(e.message);
    });
  }, []);

  return (
    <SiteLayout>
      <div className="wrap head-block narrow login-page">
        <h1>Try {brand.name}</h1>
        {member ? (
          <>
            <p className="lead">You are signed in to your society. Trying the demo signs you out of it.</p>
            <div className="login-action">
              <button type="button" className="cta" onClick={() => startDemo(true).catch((e: Error) => setProblem(e.message))}>
                Sign out and try the demo
              </button>
              <a className="cta cta-line" href="/app">Back to your control room</a>
            </div>
          </>
        ) : problem ? (
          <>
            <p className="login-problem" role="alert">{problem}</p>
            <div className="login-action"><Link className="cta cta-line" to="/">Back to the home page</Link></div>
          </>
        ) : (
          <p className="lead" role="status">Setting up a demo society with live cameras for you.</p>
        )}
      </div>
    </SiteLayout>
  );
}

export default DemoStart;
