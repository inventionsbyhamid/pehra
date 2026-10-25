import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, frameUrl } from "../api";
import { LiveView, type LiveHandle } from "../components/LiveView";
import { Thumb } from "../components/Thumb";
import { useWebcam } from "../components/useWebcam";
import { count, dayLabel, plain, span } from "../format";
import { useLoad, useTitle } from "../hooks";
import brand from "../site/brand";
import type { Answer, Camera, StateExample, WatchRow, Zone } from "../types";
import { taskDone, tasksFor, watchWords, zoneNamed, type Task } from "./setup";
import { useCan, useSession } from "./session";
import { Dialog, useDialog } from "./ui/Dialog";
import { Feed, sortIncidents } from "./ui/Feed";
import { Health } from "./ui/Health";
import { Empty, Page, PageHead, Problem, Section } from "./ui/Page";
import { SeverityTag } from "./ui/Severity";
import { purposeLabel, sentence } from "./words";
import { ZoneEditor, type Want } from "./ZoneEditor";

function Stage({ cam, liveRef, webcam }: {
  cam: Camera; liveRef: React.RefObject<LiveHandle>; webcam: ReturnType<typeof useWebcam>;
}) {
  if (cam.kind === "file") {
    return <div className="stage"><img src={frameUrl(cam.id)} alt={`A frame from ${cam.name}`} /></div>;
  }
  if (cam.kind === "webcam" && webcam.on) {
    // this browser shows its own camera at full frame rate; the server adds boxes from its analysis
    return (
      <LiveView ref={liveRef} cameraId={cam.id} zones={cam.zones} mode="overlay" video={webcam.video}
        videoActive={webcam.state === "on"} idle={webcam.state === "starting" ? "Starting this device's camera." : "Waiting for this device's camera."}>
        <video ref={webcam.video} className="stage-video" muted playsInline aria-hidden="true" />
      </LiveView>
    );
  }
  const idle = cam.kind === "webcam" ? "Waiting for this camera's device. Use this device below, or open the camera on the device it belongs to."
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
                      {t.kind === "pictures" ? "Save a picture now" : t.kind === "line" ? "Draw the line" : "Draw the zone"}
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

function EditCamera({ cam, dialog, onSaved, onZones }: {
  cam: Camera; dialog: ReturnType<typeof useDialog>; onSaved: (c: Camera) => void; onZones: () => void;
}) {
  const { purposes } = useSession();
  const navigate = useNavigate();
  const [name, setName] = useState(cam.name);
  const [purpose, setPurpose] = useState(cam.purpose ?? "other");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);

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
      onSaved(await api.patchCamera(cam.id, { name: name.trim() || cam.name, purpose }));
      dialog.close();
    });
  };

  return (
    <Dialog dialog={dialog} title={removing ? `Remove ${cam.name}?` : `Edit ${cam.name}`} id="edit-cam-title" onClose={() => setRemoving(false)}>
      {removing ? (
        <>
          <p className="small">This stops the camera and deletes its watches, events and incidents. It can't be undone.</p>
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
          <Problem>{error}</Problem>
          <div className="row">
            <button className="btn btn-primary" disabled={busy}>Save</button>
            <button type="button" className="btn" onClick={() => dialog.close()}>Cancel</button>
          </div>
          <div className="stack" style={{ gap: 4, paddingTop: 12, borderTop: "1px solid var(--line-2)" }}>
            <button type="button" className="btn btn-quiet" style={{ justifyContent: "flex-start" }} onClick={() => { dialog.close(); onZones(); }}>
              Adjust zones and lines
            </button>
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

  function onTask(task: Task) {
    if (task.kind === "zone" || task.kind === "line") openEditor({ name: task.name, kind: task.kind === "line" ? "line" : "area" });
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
        sub={<><Health camera={cam} /><span>{purposeLabel(purposes, cam.purpose)}</span></>}
        actions={admin && <button className="btn" onClick={edit.open}>Edit</button>}
      />
      {notice && <p className="notice" role="status">{notice}</p>}
      <Problem>{error ?? webcam.error}</Problem>

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
              <button className={`btn ${webcam.on ? "" : "btn-primary"}`} onClick={() => webcam.setOn(!webcam.on)}>
                {webcam.on ? "Stop this device" : "Use this device"}
              </button>
              <span className="live-note" style={{ marginTop: 0 }}>
                {webcam.state === "on"
                  ? <>Sending this device's camera to {brand.name}. Keep this page open. <span className="num">{count(webcam.sent)} sent.</span></>
                  : "This laptop or phone becomes the camera while this page is open."}
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

      {admin && <EditCamera cam={cam} dialog={edit} onSaved={(c) => camera.setData(c)} onZones={() => openEditor(null)} />}
      <PictureDialog dialog={pictureDialog} task={picture} cam={cam}
        onDone={(e) => { examples.setData([...(examples.data ?? []), e]); setNotice("Picture saved."); }} />
    </Page>
  );
}

