import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useTitle } from "../hooks";
import brand from "../site/brand";
import type { Camera } from "../types";
import { useCan, useSession } from "./session";
import { Page, PageHead, Problem } from "./ui/Page";
import { sentence } from "./words";

type How = "device" | "address";

// Ways a camera joins, in the order step 1 offers them. The recorder (NVR), bridge and phone by QR code join this
// list as they are wired; each needs a title, a line of help and what step 2 asks for.
const HOW: { id: How; title: string; text: string }[] = [
  {
    id: "device",
    title: "This device",
    text: "This laptop or phone becomes the camera. It sends pictures while its page is open.",
  },
  {
    id: "address",
    title: "A camera address",
    text: "An RTSP or HTTP address from an IP camera or a recorder that this server can reach.",
  },
];

const PARALLEL = 3;

type Kept = { text: string; state: "waiting" | "saving" | "saved" | "check" | "failed"; note?: string };

function StepLabel({ n }: { n: number }) {
  return <p className="steps-label">Step {n} of 3</p>;
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cam, setCam] = useState<Camera | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [kept, setKept] = useState<Kept[] | null>(null);

  const ideas = purposes?.[purpose]?.watches ?? [];
  const open = () => navigate(`/app/cameras/${cam!.id}${how === "device" ? "?webcam=start" : ""}`);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const made = await api.addCamera({
        name: name.trim(), kind: how === "device" ? "webcam" : "stream", purpose: purpose || "other",
        url: how === "address" ? url.trim() : undefined,
      });
      setCam(made);
      setPicked(purposes?.[purpose]?.watches ?? []);
      setStep(3);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
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

  return (
    <Page narrow>
      {step === 1 && (
        <>
          <PageHead title="How is this camera connected?" back={{ to: "/app", label: "Control room" }} sub={<StepLabel n={1} />} />
          <form className="stack" style={{ gap: 20 }} onSubmit={(e) => { e.preventDefault(); setStep(2); }}>
            <fieldset className="options">
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
          <PageHead title="Name it and say what it watches" sub={<StepLabel n={2} />} />
          <form className="stack" style={{ gap: 20 }} onSubmit={add}>
            <label className="field"><span>Name</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={60}
                placeholder={purpose && purposes?.[purpose] ? purposes[purpose]!.label : "For example: Pool"} autoFocus />
            </label>
            <fieldset className="choices">
              <legend className="label">What it watches</legend>
              {Object.entries(purposes ?? {}).map(([key, p]) => (
                <label key={key}>
                  <input type="radio" name="purpose" value={key} checked={purpose === key} onChange={() => setPurpose(key)} required />
                  {p.label}
                </label>
              ))}
            </fieldset>
            {how === "address" && (
              <label className="field"><span>Camera address</span>
                <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} required inputMode="url"
                  placeholder="rtsp://camera.example.com:554/stream1" />
                <span className="hint">Most IP cameras and recorders give one in their settings. Use the smaller substream if there is one.</span>
              </label>
            )}
            {how === "device" && (
              <p className="small muted">Your browser asks to use the camera on the next page. {brand.name} never records sound.</p>
            )}
            <Problem>{error}</Problem>
            <div className="row">
              <button className="btn btn-primary" disabled={busy || !name.trim() || !purpose}>{busy ? "Adding" : "Add camera"}</button>
              <button type="button" className="btn btn-quiet" onClick={() => setStep(1)}>Back</button>
            </div>
          </form>
        </>
      )}

      {step === 3 && cam && (
        <>
          <PageHead title="Keep the suggested watches" sub={<StepLabel n={3} />} />
          <p className="lede" style={{ marginTop: -16 }}>
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
        </>
      )}
    </Page>
  );
}
