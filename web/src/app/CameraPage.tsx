import READERS from "@data/readers";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, frameUrl, type DeviceLink } from "../api";
import { LiveView, type LiveHandle } from "../components/LiveView";
import { Thumb } from "../components/Thumb";
import type { useWebcam } from "../components/useWebcam";
import { count, dayLabel, plain, span } from "../format";
import { useLoad, useTitle } from "../hooks";
import brand from "../site/brand";
import type { Answer, Camera, StateExample, WatchRow, Zone } from "../types";
import { DeviceVideo, useDevice } from "./device";
import { readerAllowed, taskDone, tasksFor, watchWords, zoneNamed, type Task } from "./setup";
import { useCan, useSession } from "./session";
import { Copy } from "./ui/Copy";
import { Dialog, useDialog } from "./ui/Dialog";
import { Feed, sortIncidents } from "./ui/Feed";
import { Health } from "./ui/Health";
import { Empty, Page, PageHead, Problem, Section } from "./ui/Page";
import { SeverityTag } from "./ui/Severity";
import { healthHelp, purposeLabel, sentence } from "./words";
import { ZoneEditor, type Want } from "./ZoneEditor";

function Stage({ cam, liveRef, webcam }: {
  cam: Camera; liveRef: React.RefObject<LiveHandle>; webcam: ReturnType<typeof useWebcam> | null;
}) {
  const video = useRef<HTMLVideoElement>(null);
  if (cam.kind === "file") {
    return <div className="stage"><img src={frameUrl(cam.id)} alt={`A frame from ${cam.name}`} /></div>;
  }
  if (cam.kind === "webcam" && webcam?.on) {
    // this browser shows its own camera at full frame rate; the server adds boxes from its analysis
    return (
      <LiveView ref={liveRef} cameraId={cam.id} zones={cam.zones} mode="overlay" video={video}
        videoActive={webcam.state === "on"} idle={webcam.state === "starting" ? "Starting this device's camera." : "Waiting for this device's camera."}>
        <DeviceVideo stream={webcam.stream} video={video} />
      </LiveView>
    );
  }
  const idle = cam.status.source === "refused" ? "Not connected. This server won't read this camera's address."
    : cam.kind === "webcam" ? "Waiting for this camera's device. Use this device below, or open the camera on the device it belongs to."
    : cam.kind === "demo" ? "Starting the demo camera. The first picture takes a few seconds."
    : `Connecting to the camera. ${brand.name} keeps trying if it can't reach it.`;
  return <LiveView ref={liveRef} cameraId={cam.id} zones={cam.zones} mode="frames" idle={idle} />;
}

function WatchItem({ row, cam, examples, admin, onToggle, onTask }: {
  row: WatchRow; cam: Camera; examples: StateExample[]; admin: boolean;
  onToggle: (row: WatchRow, on: boolean) => void; onTask: (task: Task) => void;
}) {
  const words = sentence(plain(watchWords(row)));
  const tasks = tasksFor(row, cam);
  const severity = row.watch?.severity ?? row.severity;
  return (
    <li className={row.enabled ? undefined : "is-off"}>
      <div className="grow">
        <p className="watch-words">{words}</p>
        <p className="watch-meta">
          {(row.watch?.output ?? row.output) === "log" ? <span>Writes to the log</span> : <SeverityTag level={severity} />}
          {row.scope === "site" && <span>Every camera</span>}
          {!row.enabled && <span>Off</span>}
        </p>
        {tasks.length > 0 && (
          <ul className="list todo" aria-label={`Before "${words}" can start`}>
            {tasks.map((t) => {
              const done = taskDone(t, cam, examples);
              const blocked = t.kind === "pictures" && !zoneNamed(cam, t.zone);
              return (
                <li key={t.text} className={done ? "done" : undefined}>
                  <span>{t.text}{done && <span className="sr-only"> Done.</span>}</span>
                  {!done && admin && t.kind !== "other" && !blocked && (
                    <button type="button" className="btn btn-sm" onClick={() => onTask(t)}>
                      {t.kind === "pictures" ? "Save a picture now" : t.kind === "line" ? "Draw the line"
                        : t.kind === "reader" ? `Turn on ${t.label.toLowerCase()}` : "Draw the zone"}
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      {admin && (
        <label className="switch">
          <input type="checkbox" checked={row.enabled} onChange={(e) => onToggle(row, e.target.checked)}
            aria-label={`Watch for ${words.replace(/\.$/, "")}`} />
          <span />
        </label>
      )}
    </li>
  );
}

function LookBack({ cam }: { cam: Camera }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setAnswer(await api.ask(question.trim(), cam.id));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section title="Look back" id="lookback-title"
      aside={<Link className="small" to={`/app/cameras/${cam.id}/history`}>Who came and went</Link>}>
      <form className="lookback-form" onSubmit={ask} role="search">
        <label className="sr-only" htmlFor="lookback">Ask about this camera's footage</label>
        <input id="lookback" className="input" value={question} onChange={(e) => setQuestion(e.target.value)}
          placeholder="Who came in after 10 PM?" />
        <button className="btn" disabled={busy || !question.trim()}>{busy ? "Looking" : "Ask"}</button>
      </form>
      {busy && <p className="working" role="status"><span className="spinner" aria-hidden="true" />Reading the event log and looking at pictures</p>}
      <Problem>{error}</Problem>
      {answer && !busy && (
        <div className="card answer" aria-live="polite">
          <p className="answer-text">{plain(answer.text)}</p>
          {answer.confidence === "low" && <p className="small muted">Not sure. Check the pictures before acting on this.</p>}
          {answer.items.length > 0 && (
            <ol className="list answer-items" data-testid="answer-items">
              {answer.items.slice(0, 8).map((it, i) => (
                <li key={`${it.event_id}-${i}`} className="answer-item">
                  <Thumb src={it.thumbnail_url} alt={plain(it.label)} w={96} h={72} />
                  <div>
                    <p className="answer-label">{plain(it.label)}</p>
                    {it.start_ts != null && (
                      <p className="small muted num">{dayLabel(it.start_ts) !== "Today" && `${dayLabel(it.start_ts)} `}{span(it.start_ts, it.end_ts)}</p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </Section>
  );
}

// A camera that sends its own pictures gets a new link: the phone or bridge on the old one stops at once.
function LinkDialog({ cam, dialog }: { cam: Camera; dialog: ReturnType<typeof useDialog> }) {
  const [link, setLink] = useState<DeviceLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bridge = !!cam.url;

  async function make() {
    setBusy(true);
    setError(null);
    try {
      setLink(await api.deviceLink(cam.id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog dialog={dialog} id="link-title" onClose={() => { setLink(null); setError(null); }}
      title={link ? (bridge ? "The new bridge line" : "Open it on the phone") : bridge ? "Make a new bridge line?" : "Connect a phone?"}>
      {link ? (
        <>
          {bridge ? (
            <>
              <p className="small">Put this line in the bridge's cameras.txt in place of the old one, then start the bridge again. It holds the recorder's password, so it is shown only now.</p>
              <Copy value={link.bridge_camera ?? link.url} label="Line for cameras.txt" />
            </>
          ) : (
            <div className="qr-box">
              <img src={link.qr_url} alt="QR code for the phone's camera page" width={200} height={200} />
              <div className="stack" style={{ gap: 10 }}>
                <p className="small">Scan this with the phone's camera, open the page and press Start.</p>
                <Copy value={link.url} label="The phone's camera page" />
              </div>
            </div>
          )}
          <div><button className="btn" onClick={dialog.close}>Done</button></div>
        </>
      ) : (
        <>
          <p className="small">
            {bridge ? "A new line stops the old one at once, so the bridge needs the new line to keep sending."
              : "This makes a new QR code for a phone or laptop. Any device on the old one stops at once."}
          </p>
          <Problem>{error}</Problem>
          <div className="row">
            <button className="btn btn-primary" disabled={busy} onClick={make}>{busy ? "Making it" : bridge ? "Make a new line" : "Make a QR code"}</button>
            <button className="btn" onClick={dialog.close}>Cancel</button>
          </div>
        </>
      )}
    </Dialog>
  );
}

function EditCamera({ cam, dialog, onSaved, onZones, onLink }: {
  cam: Camera; dialog: ReturnType<typeof useDialog>; onSaved: (c: Camera) => void; onZones: () => void; onLink: () => void;
}) {
  const { purposes } = useSession();
  const navigate = useNavigate();
  const [name, setName] = useState(cam.name);
  const [purpose, setPurpose] = useState(cam.purpose ?? "other");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [reading, setReading] = useState<string[]>(cam.readers ?? []);
  useEffect(() => setReading(cam.readers ?? []), [cam.readers]);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    return run(async () => {
      const readers = cam.kind === "file" ? undefined : reading;
      onSaved(await api.patchCamera(cam.id, { name: name.trim() || cam.name, purpose, readers }));
      dialog.close();
    });
  };

  return (
    <Dialog dialog={dialog} title={removing ? `Remove ${cam.name}?` : `Edit ${cam.name}`} id="edit-cam-title" onClose={() => setRemoving(false)}>
      {removing ? (
        <>
          <p className="small">
            This stops the camera. Its watches, incidents and gate log entries, and what it saw, are deleted with it. It
            can't be undone.
          </p>
          <Problem>{error}</Problem>
          <div className="row">
            <button className="btn btn-primary" disabled={busy} onClick={() => run(async () => {
              await api.deleteCamera(cam.id);
              navigate("/app", { replace: true });
            })}>{busy ? "Removing" : "Remove camera"}</button>
            <button className="btn" onClick={() => setRemoving(false)}>Keep it</button>
          </div>
        </>
      ) : (
        <form className="stack" onSubmit={save}>
          <label className="field"><span>Name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} required />
          </label>
          <label className="field"><span>What it watches</span>
            <select className="select" value={purpose} onChange={(e) => setPurpose(e.target.value)}>
              {Object.entries(purposes ?? { other: { label: "Other", watches: [] } }).map(([k, p]) => (
                <option key={k} value={k}>{p.label}</option>
              ))}
            </select>
          </label>
          {cam.kind !== "file" && (
            <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0, gap: 10 }}>
              <legend className="label" style={{ marginBottom: 8 }}>What it reads</legend>
              {Object.entries(READERS).map(([id, r]) => {
                const not = readerAllowed(id, purpose);
                const on = reading.includes(id);
                return (
                  <label key={id} className="check">
                    <input type="checkbox" checked={on} disabled={!!not && !on}
                      onChange={(e) => setReading(e.target.checked ? [...reading, id] : reading.filter((x) => x !== id))} />
                    <span>
                      <strong>{r.label}</strong>
                      <span className="hint" style={{ display: "block" }}>{not ?? r.about}</span>
                    </span>
                  </label>
                );
              })}
            </fieldset>
          )}
          <Problem>{error}</Problem>
          <div className="row">
            <button className="btn btn-primary" disabled={busy}>Save</button>
            <button type="button" className="btn" onClick={() => dialog.close()}>Cancel</button>
          </div>
          <div className="stack" style={{ gap: 4, paddingTop: 12, borderTop: "1px solid var(--line-2)" }}>
            <button type="button" className="btn btn-quiet" style={{ justifyContent: "flex-start" }} onClick={() => { dialog.close(); onZones(); }}>
              Adjust zones and lines
            </button>
            {cam.kind === "webcam" && (
              <button type="button" className="btn btn-quiet" style={{ justifyContent: "flex-start" }} onClick={() => { dialog.close(); onLink(); }}>
                {cam.url ? "Make a new bridge line" : "Connect a phone"}
              </button>
            )}
            {cam.kind !== "file" && (
              <button type="button" className="btn btn-quiet" style={{ justifyContent: "flex-start" }} disabled={busy}
                onClick={() => run(async () => { onSaved(await api.patchCamera(cam.id, { enabled: !cam.enabled })); dialog.close(); })}>
                {cam.enabled ? "Pause this camera" : "Resume this camera"}
              </button>
            )}
            <button type="button" className="btn btn-quiet btn-danger" style={{ justifyContent: "flex-start" }} onClick={() => setRemoving(true)}>
              Remove this camera
            </button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

function PictureDialog({ dialog, task, cam, onDone }: {
  dialog: ReturnType<typeof useDialog>; task: Extract<Task, { kind: "pictures" }> | null; cam: Camera;
  onDone: (e: StateExample) => void;
}) {
  const [state, setState] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setState(task && !task.not ? task.state : "");
    setError(null);
  }, [task]);
  if (!task) return <Dialog dialog={dialog} title="Save a picture" id="pic-title"><span /></Dialog>;
  const zone = zoneNamed(cam, task.zone);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!zone?.id || !state.trim()) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api.addExample(cam.id, zone.id, state.trim()));
      dialog.close();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog dialog={dialog} title={`A picture of the ${task.zone}`} id="pic-title">
      <form className="stack" onSubmit={save}>
        <p className="small">
          {task.not ? `Make sure the ${task.zone} is not ${task.state} now, then say how it is.` : `Make sure the ${task.zone} is ${task.state} now.`}
          {" "}The camera's picture right now is saved as an example. Save one by day and one at night.
        </p>
        <label className="field"><span>The {task.zone} is</span>
          <input className="input" value={state} onChange={(e) => setState(e.target.value)} placeholder="For example: open" required />
        </label>
        <Problem>{error}</Problem>
        <div className="row">
          <button className="btn btn-primary" disabled={busy || !state.trim()}>{busy ? "Saving" : "Save the picture"}</button>
          <button type="button" className="btn" onClick={() => dialog.close()}>Cancel</button>
        </div>
      </form>
    </Dialog>
  );
}

export default function CameraPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const { purposes } = useSession();
  const admin = useCan("admin");
  const camera = useLoad(() => api.camera(id), [id], 10_000);
  const watches = useLoad(() => api.watches(id), [id]);
  const incidents = useLoad(() => api.incidents({ camera_id: id }), [id], 15_000);
  const examples = useLoad(() => api.examples(id), [id]);
  const [editing, setEditing] = useState<{ background: string | null; want: Want | null } | null>(null);
  const [saving, setSaving] = useState(false);
  const [zoneError, setZoneError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>((location.state as { notice?: string } | null)?.notice ?? null);
  const [error, setError] = useState<string | null>(null);
  const [picture, setPicture] = useState<Extract<Task, { kind: "pictures" }> | null>(null);
  const liveRef = useRef<LiveHandle>(null);
  const edit = useDialog();
  const pictureDialog = useDialog();
  const linkDialog = useDialog();
  const device = useDevice();
  const webcam = device.camera?.id === id ? device.webcam : null;
  const cam = camera.data;
  useTitle(cam?.name ?? "Camera");

  useEffect(() => {
    if (cam?.kind === "webcam" && params.get("webcam") === "start") {
      device.start(cam.id, cam.name);
      params.delete("webcam");
      setParams(params, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cam?.kind]);

  const names = useMemo(() => (cam ? { [cam.id]: cam.name } : {}), [cam]);

  if (camera.loading && !cam) return <Page><p className="muted">Loading the camera</p></Page>;
  if (!cam) {
    return (
      <Page>
        <PageHead title="Camera not found" back={{ to: "/app", label: "Control room" }} />
        <Problem>{camera.error ?? "There is no camera at this address. It may have been removed."}</Problem>
      </Page>
    );
  }

  function openEditor(want: Want | null) {
    const snap = cam!.kind === "file" ? null : liveRef.current?.snapshot();
    setZoneError(null);
    setEditing({ background: snap ?? frameUrl(cam!.id, String(Date.now())), want });
  }

  async function onTask(task: Task) {
    if (task.kind === "reader") {
      setError(null);
      try {
        camera.setData(await api.setReaders(cam!.id, [...new Set([...(cam!.readers ?? []), task.reader])]));
        setNotice(`${task.label} is on. Change it in Edit.`);
      } catch (e) {
        setError((e as Error).message);
      }
    } else if (task.kind === "zone" || task.kind === "line") openEditor({ name: task.name, kind: task.kind === "line" ? "line" : "area" });
    else if (task.kind === "pictures") {
      setPicture(task);
      pictureDialog.open();
    }
  }

  async function saveZones(zones: Zone[]) {
    setSaving(true);
    setZoneError(null);
    try {
      const updated = await api.patchCamera(cam!.id, { zones });
      camera.setData(updated);
      setEditing(null);
      const off = updated.disabled_watches ?? [];
      setNotice(off.length ? `Saved. These watches were turned off because their zone is gone: ${off.join(", ")}.` : "Saved.");
      watches.reload();
    } catch (e) {
      setZoneError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function toggle(row: WatchRow, on: boolean) {
    setError(null);
    try {
      const updated = await api.toggleWatch(row.id, on);
      watches.setData((watches.data ?? []).map((w) => (w.id === row.id ? { ...w, ...updated } : w)));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const list = watches.data ?? [];
  const recent = sortIncidents(incidents.data ?? []);
  const ideas = purposes?.[cam.purpose ?? "other"]?.watches ?? [];

  return (
    <Page>
      <PageHead
        back={{ to: "/app", label: "Control room" }}
        title={cam.name}
        sub={<>
          <Health camera={cam} />
          <span>{purposeLabel(purposes, cam.purpose)}</span>
          {(cam.readers ?? []).filter((r) => READERS[r]).map((r) => <span key={r}>{READERS[r]!.label} on</span>)}
        </>}
        actions={admin && <button className="btn" onClick={edit.open}>Edit</button>}
      />
      {notice && <p className="notice" role="status">{notice}</p>}
      {healthHelp(cam) && <p className="notice notice-warn">{healthHelp(cam)}</p>}
      <Problem>{error ?? webcam?.error}</Problem>

      <div className="campage">
        <div className="campage-live">
          {editing ? (
            <ZoneEditor background={editing.background} zones={cam.zones} want={editing.want} saving={saving} error={zoneError}
              onSave={saveZones} onCancel={() => setEditing(null)} />
          ) : (
            <Stage cam={cam} liveRef={liveRef} webcam={webcam} />
          )}
          {!editing && cam.kind === "webcam" && (
            <div className="row" style={{ marginTop: 12 }}>
              <button className={`btn ${webcam?.on ? "" : "btn-primary"}`}
                onClick={() => (webcam?.on ? device.stop() : device.start(cam.id, cam.name))}>
                {webcam?.on ? "Stop this device" : "Use this device"}
              </button>
              <span className="live-note" style={{ marginTop: 0 }}>
                {webcam?.state === "on"
                  ? <>Sending this device's camera to {brand.name}. Keep this tab open. <span className="num">{count(webcam.sent)} sent.</span></>
                  : "This laptop or phone becomes the camera while this tab is open."}
              </span>
            </div>
          )}
        </div>

        <div className="campage-side">
          <Section title="Watches" id="watches-title"
            aside={admin && list.length > 0 && <Link className="btn btn-primary btn-sm" to={`/app/watches/new?camera=${cam.id}`}>Add a watch</Link>}>
            {watches.loading && !watches.data ? null : list.length === 0 ? (
              <Empty title="No watches yet"
                action={admin ? <Link className="btn btn-primary" to={`/app/watches/new?camera=${cam.id}`}>Add a watch</Link> : undefined}>
                {`Tell ${brand.name} what to watch for in plain words`}{ideas[0] ? `, like: ${ideas[0]}.` : "."}
              </Empty>
            ) : (
              <ul className="list watch-list">
                {list.map((w) => (
                  <WatchItem key={w.id} row={w} cam={cam} examples={examples.data ?? []} admin={admin} onToggle={toggle} onTask={onTask} />
                ))}
              </ul>
            )}
          </Section>

          <Section title="Recent incidents" id="recent-title">
            {recent.length === 0 ? (
              <p className="small muted">Nothing from this camera yet. When a watch spots something, it shows here.</p>
            ) : (
              <div className="card" style={{ padding: "6px 16px" }}>
                <Feed incidents={recent} names={names} label={`Incidents from ${cam.name}`} limit={5} />
              </div>
            )}
          </Section>
        </div>

        <div className="campage-back"><LookBack cam={cam} /></div>
      </div>

      {admin && <EditCamera cam={cam} dialog={edit} onSaved={(c) => camera.setData(c)} onZones={() => openEditor(null)} onLink={linkDialog.open} />}
      {admin && cam.kind === "webcam" && <LinkDialog cam={cam} dialog={linkDialog} />}
      <PictureDialog dialog={pictureDialog} task={picture} cam={cam}
        onDone={(e) => { examples.setData([...(examples.data ?? []), e]); setNotice("Picture saved."); }} />
    </Page>
  );
}

