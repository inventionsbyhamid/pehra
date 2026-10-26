import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, upload, type DeviceLink } from "../api";
import { clockDuration } from "../format";
import { useLoad, useTitle } from "../hooks";
import brand from "../site/brand";
import type { Camera, Scan } from "../types";
import { useCan, useSession } from "./session";
import { Choices } from "./ui/Choices";
import { Copy } from "./ui/Copy";
import { Page, PageHead, Problem } from "./ui/Page";
import { sentence } from "./words";

type How = "device" | "phone" | "recorder" | "bridge" | "address" | "upload";

// The ways a camera joins, in the order step 1 offers them.
const HOW: { id: How; title: string; text: string }[] = [
  { id: "device", title: "This device", text: "This laptop or phone becomes the camera. It sends pictures while its tab is open." },
  { id: "phone", title: "A phone", text: "Scan a QR code with a spare phone. It becomes the camera while its page is open." },
  { id: "recorder", title: "A recorder", text: "A Hikvision, CP Plus or Dahua NVR or DVR that this server can reach over the internet." },
  { id: "bridge", title: "Through the bridge", text: "A recorder on the society's own network. A small program on a computer there sends the pictures." },
  { id: "address", title: "A camera address", text: "An RTSP address (rtsp:// or rtsps://) of an IP camera that this server can reach." },
  { id: "upload", title: "Upload a recording", text: `A video file. ${brand.name} scans it once, then you can ask what happened in it.` },
];

const PARALLEL = 3;
const SCAN_POLL_MS = 1000;

type Kept = { text: string; state: "waiting" | "saving" | "saved" | "check" | "failed"; note?: string };
type Recorder = { brand: string; host: string; channel: string; user: string; password: string; port: string };

function StepLabel({ n }: { n: number }) {
  return <p className="steps-label">Step {n} of 3</p>;
}

function RecorderFields({ value, onChange, bridge }: { value: Recorder; onChange: (r: Recorder) => void; bridge: boolean }) {
  const brands = useLoad(() => api.nvr(), []);
  const list = brands.data?.brands ?? [];
  const picked = list.find((b) => b.id === value.brand);
  const set = (patch: Partial<Recorder>) => onChange({ ...value, ...patch });
  useEffect(() => {
    if (!value.brand && list[0]) set({ brand: list[0].id });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list.length]);
  return (
    <>
      <Problem>{brands.error}</Problem>
      {list.length > 0 && (
        <div>
          <Choices legend="Recorder brand" value={value.brand} options={list.map((b) => ({ value: b.id, label: b.label }))}
            onChange={(b) => set({ brand: b })} />
          {picked?.hint && <p className="hint" style={{ marginTop: 8 }}>{picked.hint}</p>}
        </div>
      )}
      <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
        <label className="field" style={{ flex: "3 1 14rem" }}><span>Recorder address</span>
          <input className="input" value={value.host} onChange={(e) => set({ host: e.target.value })} required
            placeholder={bridge ? "For example: 192.168.1.64" : "For example: gate.example.com"} autoComplete="off" spellCheck={false} />
        </label>
        <label className="field" style={{ flex: "1 1 7rem" }}><span>Camera number</span>
          <input className="input" type="number" min={1} max={256} value={value.channel} onChange={(e) => set({ channel: e.target.value })} required />
        </label>
      </div>
      <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
        <label className="field" style={{ flex: "1 1 11rem" }}><span>User</span>
          <input className="input" value={value.user} onChange={(e) => set({ user: e.target.value })} autoComplete="off" spellCheck={false} />
        </label>
        <label className="field" style={{ flex: "1 1 11rem" }}><span>Password</span>
          <input className="input" type="password" value={value.password} onChange={(e) => set({ password: e.target.value })} autoComplete="new-password" />
        </label>
      </div>
      <details>
        <summary>Another port</summary>
        <label className="field" style={{ marginTop: 10, maxWidth: "12rem" }}><span>Port</span>
          <input className="input" type="number" min={1} max={65535} value={value.port} placeholder={String(picked?.port ?? 554)}
            onChange={(e) => set({ port: e.target.value })} />
        </label>
      </details>
      <p className="hint">
        {bridge
          ? "Use a user that can only view. The address is on the society's network: only the bridge reads it, and the password is stored encrypted."
          : "Use a user that can only view. The recorder needs an address this server can reach; one only on the society's network goes through the bridge."}
      </p>
    </>
  );
}

function PhoneLink({ link }: { link: DeviceLink }) {
  return (
    <section className="card stack" aria-labelledby="phone-title">
      <h2 id="phone-title" className="h2">Open it on the phone</h2>
      <div className="qr-box">
        <img src={link.qr_url} alt="QR code for the phone's camera page" width={200} height={200} />
        <div className="stack" style={{ gap: 10 }}>
          <p>Scan this with the phone's camera, open the page and press Start. Keep the phone on charge, facing the area.</p>
          <Copy value={link.url} label="The phone's camera page" />
          <p className="hint">The link works for this camera only. A new one, from Edit on the camera's page, stops the old phone.</p>
        </div>
      </div>
    </section>
  );
}

function BridgeLink({ link }: { link: DeviceLink }) {
  const server = window.location.origin;
  return (
    <section className="card stack" aria-labelledby="bridge-title">
      <h2 id="bridge-title" className="h2">Set up the bridge</h2>
      <ol className="steps-list">
        <li>
          On a computer at the society that stays on, get the bridge.{" "}
          <a href="/bridge/camera-bridge.zip" download>Download the bridge</a>, then follow{" "}
          <a href="/bridge/README.md" target="_blank" rel="noreferrer">the bridge steps</a>.
        </li>
        <li className="stack" style={{ gap: 8 }}>
          <span>Put this line in its cameras.txt file. It holds the recorder's password, so it is shown only now.</span>
          {link.bridge_camera ? <Copy value={link.bridge_camera} label="Line for cameras.txt" /> : <Copy value={link.url} label="Camera link" />}
        </li>
        <li className="stack" style={{ gap: 8 }}>
          <span>Start the bridge with this control room's address.</span>
          <Copy value={`--server ${server}`} label="Control room address for the bridge" />
        </li>
      </ol>
      <p className="hint">Lost the line? Choose Edit on the camera's page to make a new one. The old line then stops.</p>
    </section>
  );
}

function Scanning({ scanId, cam }: { scanId: string; cam: Camera }) {
  const [scan, setScan] = useState<Scan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  useEffect(() => {
    let gone = false;
    let timer = 0;
    const poll = async () => {
      try {
        const s = await api.scan(scanId);
        if (gone) return;
        setScan(s);
        if (s.state === "scanning" || s.state === "checking") timer = window.setTimeout(poll, SCAN_POLL_MS);
      } catch (e) {
        if (!gone) setError((e as Error).message);
      }
    };
    poll();
    return () => {
      gone = true;
      window.clearTimeout(timer);
    };
  }, [scanId]);
  const share = Math.round((scan?.progress ?? 0) * 100);
  return (
    <section className="card stack" aria-labelledby="scan-title">
      <h2 id="scan-title" className="h2">{cam.name}</h2>
      <Problem>{error ?? (scan?.state === "failed" ? `The scan stopped: ${scan.error ?? "the video could not be read"}.` : null)}</Problem>
      {(!scan || scan.state === "scanning") && (
        <div className="stack" style={{ gap: 8 }}>
          <div className="progress" role="progressbar" aria-label="Scanning" aria-valuenow={share} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${share}%` }} />
          </div>
          <p className="working small"><span className="spinner" aria-hidden="true" />Looking through the video, {share}% done</p>
        </div>
      )}
      {scan?.state === "checking" && <p className="working small"><span className="spinner" aria-hidden="true" />Checking what it found</p>}
      {scan?.state === "done" && (
        <>
          <p role="status">
            Scanned {scan.stats ? `${clockDuration(scan.stats.duration_s)} of video in ${Math.max(1, Math.round(scan.stats.wall_s))} s` : "the video"}.
            {" "}Ask what happened in it on its page.
          </p>
          <div><button className="btn btn-primary" onClick={() => navigate(`/app/cameras/${cam.id}`)}>Open {cam.name}</button></div>
        </>
      )}
    </section>
  );
}

export default function AddCamera() {
  useTitle("Add a camera");
  const navigate = useNavigate();
  const { purposes } = useSession();
  const admin = useCan("admin");
  const [step, setStep] = useState(1);
  const [how, setHow] = useState<How>("device");
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [url, setUrl] = useState("");
  const [recorder, setRecorder] = useState<Recorder>({ brand: "", host: "", channel: "1", user: "", password: "", port: "" });
  const [file, setFile] = useState<File | null>(null);
  const [sent, setSent] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cam, setCam] = useState<(Camera & { notice?: string | null }) | null>(null);
  const [link, setLink] = useState<DeviceLink | null>(null);
  const [scanId, setScanId] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [kept, setKept] = useState<Kept[] | null>(null);

  const ideas = purposes?.[purpose]?.watches ?? [];
  const recording = how === "upload";
  const open = () => navigate(`/app/cameras/${cam!.id}${how === "device" ? "?webcam=start" : ""}`);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (recording) {
        const form = new FormData();
        form.append("file", file!);
        form.append("name", name.trim() || file!.name.replace(/\.[^.]+$/, ""));
        setSent(0);
        const made = await upload<{ camera_id: string; scan_id: string }>("/api/uploads", form, setSent);
        setCam(await api.camera(made.camera_id));
        setScanId(made.scan_id);
        setStep(3);
        return;
      }
      let made: Camera & { notice?: string | null };
      if (how === "recorder" || how === "bridge") {
        made = await api.addRecorder({
          name: name.trim(), purpose: purpose || "other", bridge: how === "bridge",
          nvr: {
            brand: recorder.brand, host: recorder.host.trim(), channel: Number(recorder.channel) || 1,
            user: recorder.user.trim(), password: recorder.password, ...(recorder.port ? { port: Number(recorder.port) } : {}),
          },
        });
      } else {
        made = await api.addCamera({
          name: name.trim(), kind: how === "address" ? "stream" : "webcam", purpose: purpose || "other",
          url: how === "address" ? url.trim() : undefined,
        });
      }
      setCam(made);
      if (how === "phone" || how === "bridge") setLink(await api.deviceLink(made.id));
      setPicked(purposes?.[purpose]?.watches ?? []);
      setStep(3);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      setSent(null);
    }
  }

  async function keep() {
    if (!cam) return;
    const rows: Kept[] = picked.map((text) => ({ text, state: "waiting" }));
    setKept(rows);
    const update = (i: number, patch: Partial<Kept>) => setKept((k) => k!.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    let next = 0;
    async function worker() {
      while (next < rows.length) {
        const i = next++;
        update(i, { state: "saving" });
        try {
          const r = await api.saveWatch(cam!.id, { text: rows[i]!.text });
          update(i, r.saved ? { state: "saved" } : { state: "check", note: r.problem ?? undefined });
        } catch (err) {
          update(i, { state: "failed", note: (err as Error).message });
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(PARALLEL, rows.length) }, worker));
  }

  if (!admin) {
    return (
      <Page narrow>
        <PageHead title="Add a camera" back={{ to: "/app", label: "Control room" }} />
        <p className="notice">Only a society admin can add cameras. Ask your admin to add this one.</p>
      </Page>
    );
  }

  const done = kept?.every((k) => k.state !== "waiting" && k.state !== "saving");
  const ready = recording ? !!file
    : !!name.trim() && !!purpose && (how !== "address" || !!url.trim()) && ((how !== "recorder" && how !== "bridge") || !!recorder.host.trim());
  const title3 = how === "phone" ? "Open it on the phone" : how === "bridge" ? "Set up the bridge" : recording ? "Scanning the recording" : "Keep the suggested watches";

  return (
    <Page narrow>
      {step === 1 && (
        <>
          <PageHead title="How is this camera connected?" back={{ to: "/app", label: "Control room" }} sub={<StepLabel n={1} />} />
          <form className="stack" style={{ gap: 20 }} onSubmit={(e) => { e.preventDefault(); setError(null); setStep(2); }}>
            <fieldset className="options options-2">
              <legend className="sr-only">How the camera connects</legend>
              {HOW.map((h) => (
                <label key={h.id} className="option">
                  <input type="radio" name="how" value={h.id} checked={how === h.id} onChange={() => setHow(h.id)} />
                  <strong>{h.title}</strong>
                  <span>{h.text}</span>
                </label>
              ))}
            </fieldset>
            <div><button className="btn btn-primary">Next</button></div>
          </form>
        </>
      )}

      {step === 2 && (
        <>
          <PageHead title={recording ? "Pick the recording" : "Name it and say what it watches"} sub={<StepLabel n={2} />} />
          <form className="stack" style={{ gap: 20 }} onSubmit={add}>
            {recording && (
              <div className="field">
                <label htmlFor="video-file" className="label">Video file</label>
                <input id="video-file" type="file" accept="video/*" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
                <span className="hint">From a recorder's export or a phone. It is scanned once, here on the server.</span>
              </div>
            )}
            <label className="field"><span>{recording ? "Name (optional)" : "Name"}</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} required={!recording} maxLength={60}
                placeholder={recording ? "For example: Gate, Saturday night" : purpose && purposes?.[purpose] ? purposes[purpose]!.label : "For example: Pool"}
                autoFocus />
            </label>
            {!recording && (
              <fieldset className="choices">
                <legend className="label">What it watches</legend>
                {Object.entries(purposes ?? {}).map(([key, p]) => (
                  <label key={key}>
                    <input type="radio" name="purpose" value={key} checked={purpose === key} onChange={() => setPurpose(key)} required />
                    {p.label}
                  </label>
                ))}
              </fieldset>
            )}
            {how === "address" && (
              <label className="field"><span>Camera address</span>
                <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} required inputMode="url" spellCheck={false}
                  placeholder="rtsp://camera.example.com:554/stream1" />
                <span className="hint">It starts with rtsp:// or rtsps://. Most IP cameras give one in their settings; use the smaller substream if there is one. A camera only on the society's network goes through the bridge.</span>
              </label>
            )}
            {(how === "recorder" || how === "bridge") && <RecorderFields value={recorder} onChange={setRecorder} bridge={how === "bridge"} />}
            {how === "device" && (
              <p className="small muted">Your browser asks to use the camera on the next page. {brand.name} never records sound.</p>
            )}
            {how === "phone" && <p className="small muted">Next you get a QR code to scan with the phone.</p>}
            <Problem>{error}</Problem>
            {sent !== null && (
              <div className="progress" role="progressbar" aria-label="Uploading" aria-valuenow={Math.round(sent * 100)} aria-valuemin={0} aria-valuemax={100}>
                <span style={{ width: `${Math.round(sent * 100)}%` }} />
              </div>
            )}
            <div className="row">
              <button className="btn btn-primary" disabled={busy || !ready}>
                {busy ? (recording ? "Uploading" : "Adding") : recording ? "Upload and scan" : "Add camera"}
              </button>
              <button type="button" className="btn btn-quiet" onClick={() => setStep(1)}>Back</button>
            </div>
          </form>
        </>
      )}

      {step === 3 && cam && (
        <>
          <PageHead title={title3} sub={<StepLabel n={3} />} />
          {cam.notice && <p className="notice notice-warn" role="status">{cam.notice}</p>}
          {link && how === "phone" && <PhoneLink link={link} />}
          {link && how === "bridge" && <BridgeLink link={link} />}
          {scanId && <Scanning scanId={scanId} cam={cam} />}
          {!recording && (
            <section className="stack" style={{ gap: 16 }} aria-labelledby="keep-title">
              {how === "phone" || how === "bridge" ? (
                <h2 id="keep-title" className="h2">Suggested watches</h2>
              ) : <span id="keep-title" className="sr-only">Suggested watches</span>}
              <p className="lede" style={{ marginTop: how === "phone" || how === "bridge" ? -8 : -16 }}>
                {cam.name} is added. {ideas.length ? "These suit what it watches. Keep the ones you want; you can add your own later." : "Add watches in plain words from the camera's page."}
              </p>
              {kept ? (
                <>
                  <ul className="list keep-list card" aria-live="polite">
                    {kept.map((k) => (
                      <li key={k.text}>
                        <span>{sentence(k.text)}{k.note && k.state !== "saved" && <span className="hint" style={{ display: "block" }}>{k.note}</span>}</span>
                        <span className="keep-state">
                          {k.state === "saving" || k.state === "waiting" ? "Reading" : k.state === "saved" ? "Kept" : k.state === "check" ? "Needs a look" : "Not added"}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {done && (
                    <div className="row">
                      <button className="btn btn-primary" onClick={open}>Open {cam.name}</button>
                      {kept.some((k) => k.state === "check" || k.state === "failed") && (
                        <Link className="btn" to={`/app/watches/new?camera=${cam.id}`}>Add a watch yourself</Link>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <>
                  {ideas.length > 0 && (
                    <fieldset className="card stack" style={{ gap: 12 }}>
                      <legend className="sr-only">Suggested watches</legend>
                      {ideas.map((idea) => (
                        <label key={idea} className="check">
                          <input type="checkbox" checked={picked.includes(idea)}
                            onChange={(e) => setPicked(e.target.checked ? [...picked, idea] : picked.filter((p) => p !== idea))} />
                          <span>{sentence(idea)}</span>
                        </label>
                      ))}
                    </fieldset>
                  )}
                  <div className="row">
                    {picked.length > 0 && (
                      <button className="btn btn-primary" onClick={keep}>Keep {picked.length === 1 ? "this watch" : `these ${picked.length} watches`}</button>
                    )}
                    <button className={`btn${picked.length ? " btn-quiet" : " btn-primary"}`} onClick={open}>
                      {picked.length ? "Skip for now" : `Open ${cam.name}`}
                    </button>
                  </div>
                </>
              )}
            </section>
          )}
        </>
      )}
    </Page>
  );
}
