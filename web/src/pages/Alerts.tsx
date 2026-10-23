import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, blobUrl, clipUrl } from "../api";
import { AlertSummary } from "../components/AlertSummary";
import { AlertStatusTag, SeverityTag } from "../components/Tags";
import { Trace } from "../components/Trace";
import { WatchSentence } from "../components/WatchSentence";
import { plain, when } from "../format";
import { useLoad, useTitle } from "../hooks";
import type { Alert, Camera, WatchRow } from "../types";

type Filter = "needs" | "all";

function Clip({ alert }: { alert: Alert }) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    setFailed(false);
    setAttempt(0);
  }, [alert.id]);
  return (
    <div className="clip">
      {failed ? (
        <div className="stage">
          {alert.keyframe_key && <img className="clip-key" src={blobUrl(alert.keyframe_key)!} alt="The moment that was flagged" />}
          <div className="clip-missing">
            <p>The clip could not be played. It may still be being written, so try again in a few seconds.</p>
            <button className="btn btn-sm" onClick={() => { setFailed(false); setAttempt(attempt + 1); }}>Try again</button>
          </div>
        </div>
      ) : (
        <video key={`${alert.id}-${attempt}`} className="clip-video" controls playsInline preload="metadata"
          src={`${clipUrl(alert.id)}${attempt ? `?try=${attempt}` : ""}`}
          onError={() => setFailed(true)} aria-label="Clip from a few seconds before to a few seconds after the moment" />
      )}
    </div>
  );
}

function Detail({ alert, camera, watch, people, onChange }: {
  alert: Alert; camera?: Camera; watch?: WatchRow; people: Record<string, string>; onChange: (a: Alert) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);
  useEffect(() => {
    setError(null);
    setOutcome(null);
  }, [alert.id]);

  const decided = alert.status === "approved" || alert.status === "declined";
  const needsDecision = !decided && (alert.status === "awaiting_human" || (alert.severity === "critical" && alert.status !== "dismissed"));
  const question = alert.trace.find((t) => t.tool === "ask_human")?.args.question as string | undefined;

  async function act(kind: "approve" | "decline" | "up" | "down") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "approve") {
        const r = await api.approve(alert.id);
        onChange({ ...alert, status: "approved" });
        setOutcome(r.notified ? "Approved. Your contact has been sent a notice." : "Approved. No new notice was sent, because one went out in the last few minutes.");
      } else if (kind === "decline") {
        await api.decline(alert.id);
        onChange({ ...alert, status: "declined" });
        setOutcome("Declined. Nobody was contacted.");
      } else {
        await api.feedback(alert.id, kind);
        onChange({ ...alert, feedback: kind });
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <article className="alert-detail" aria-labelledby="alert-title">
      <Link to="/alerts" className="small back only-narrow">All alerts</Link>
      <Clip alert={alert} />
      <div className="stack" style={{ gap: 10 }}>
        <div className="row" style={{ gap: 6 }}>
          <SeverityTag severity={alert.severity} />
          <AlertStatusTag status={alert.status} />
        </div>
        <h2 id="alert-title" className="alert-title">{plain(alert.summary)}</h2>
        <p className="small muted">
          <time dateTime={new Date(alert.ts * 1000).toISOString()} className="num">{when(alert.ts)}</time>
          {camera && <> on <Link to={`/cameras/${camera.id}`}>{camera.name}</Link></>}
        </p>
      </div>

      {alert.status === "unverified" && (
        <div className="notice notice-warn">
          <p><strong>Not checked by AI.</strong> The AI could not look at this one, so it is shown as OpenCV saw it. Watch the clip before you act.</p>
        </div>
      )}
      {/intruder/i.test(watch?.watch.name ?? alert.summary) && (
        <p className="small muted">Possible intruder alerts are about behaviour only, never how someone looks. A person decides what happens next.</p>
      )}

      {needsDecision && (
        <section className="decision" aria-labelledby="decide-title">
          <h3 id="decide-title" className="h3">{question ? plain(question) : "Should Watchful tell your contact about this?"}</h3>
          <p className="small">Approving sends a notice to the caregiver contact. Nothing goes out unless you approve.</p>
          <div className="row">
            <button className="btn btn-primary" disabled={!!busy} onClick={() => act("approve")}>{busy === "approve" ? "Approving" : "Approve and notify"}</button>
            <button className="btn" disabled={!!busy} onClick={() => act("decline")}>{busy === "decline" ? "Declining" : "Decline"}</button>
          </div>
        </section>
      )}
      {outcome && <p className="notice notice-good" role="status">{outcome}</p>}
      {error && <p className="notice notice-error" role="alert">{error}</p>}

      <div className="feedback">
        <span className="small">Was this alert right?</span>
        <div className="row" style={{ gap: 6 }}>
          <button className="btn btn-sm" aria-pressed={alert.feedback === "up"} disabled={!!busy} onClick={() => act("up")}>
            <Thumbs up /> Yes
          </button>
          <button className="btn btn-sm" aria-pressed={alert.feedback === "down"} disabled={!!busy} onClick={() => act("down")}>
            <Thumbs /> No
          </button>
        </div>
        {alert.feedback && <span className="xsmall muted">Thanks, noted.</span>}
      </div>

      {watch && (
        <section className="section">
          <h3 className="h3">The watch</h3>
          <WatchSentence watch={watch.watch} people={people} small />
        </section>
      )}

      <section className="section">
        <h3 className="h3">How the AI checked it</h3>
        <Trace steps={alert.trace} />
      </section>
    </article>
  );
}

function Thumbs({ up }: { up?: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" style={{ transform: up ? undefined : "rotate(180deg)" }}>
      <path d="M5 14H2.5V7H5m0 7h6.3a1.5 1.5 0 0 0 1.5-1.2l.9-4.5A1.5 1.5 0 0 0 12.2 6.5H9.5V3.2A1.7 1.7 0 0 0 7.8 1.5L5 7v7Z"
        fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  );
}

export default function Alerts() {
  useTitle("Alerts");
  const { id } = useParams();
  const [filter, setFilter] = useState<Filter>("all");
  const [showDismissed, setShowDismissed] = useState(false);
  const [cameraId, setCameraId] = useState("");
  const alerts = useLoad(() => api.alerts(cameraId || undefined, showDismissed), [cameraId, showDismissed], 10000);
  const cameras = useLoad(() => api.cameras(), []);
  const people = useLoad(() => api.people(), []);
  const [watches, setWatches] = useState<Record<string, WatchRow>>({});

  useEffect(() => {
    if (!cameras.data) return;
    Promise.all(cameras.data.map((c) => api.watches(c.id).catch(() => [] as WatchRow[])))
      .then((all) => setWatches(Object.fromEntries(all.flat().map((w) => [w.id, w]))));
  }, [cameras.data]);

  const camById = useMemo(() => Object.fromEntries((cameras.data ?? []).map((c) => [c.id, c])), [cameras.data]);
  const peopleMap = useMemo(() => Object.fromEntries((people.data ?? []).map((p) => [p.person_id, p.name])), [people.data]);
  const all = alerts.data ?? [];
  const needs = all.filter((a) => a.status === "awaiting_human" || a.status === "unverified");
  const shown = filter === "needs" ? needs : all;
  const selected = all.find((a) => a.id === id) ?? (id ? undefined : shown[0]);

  function update(a: Alert) {
    alerts.setData(all.map((x) => (x.id === a.id ? a : x)));
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Alerts</h1>
          <p className="lede">Each alert is a moment a watch flagged and the AI looked at again. Serious ones wait for you before anyone is contacted.</p>
        </div>
      </div>

      <div className={`alerts-layout${id ? " has-selection" : ""}`}>
        <section className="alerts-list stack" aria-label="Alert list">
          <div className="row filters">
            <div className="segmented" role="group" aria-label="Which alerts">
              <button aria-pressed={filter === "all"} onClick={() => setFilter("all")}>All</button>
              <button aria-pressed={filter === "needs"} onClick={() => setFilter("needs")}>
                Needs a look{needs.length > 0 && <span className="num"> ({needs.length})</span>}
              </button>
            </div>
            <select className="select select-sm" aria-label="Camera" value={cameraId} onChange={(e) => setCameraId(e.target.value)}>
              <option value="">All cameras</option>
              {(cameras.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <label className="check small"><input type="checkbox" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} />Show dismissed</label>
          </div>
          {alerts.error && <p className="notice notice-error" role="alert">{alerts.error}</p>}
          {alerts.loading && !alerts.data ? <p className="muted">Loading alerts</p> : shown.length === 0 ? (
            <div className="empty">
              <p>{filter === "needs" ? "Nothing is waiting for you." : "No alerts yet."}</p>
              <p className="small">Add a watch on a camera and alerts will appear here once the AI has checked them.</p>
            </div>
          ) : (
            <div className="alert-list">
              {shown.map((a) => <AlertSummary key={a.id} alert={a} camera={camById[a.camera_id]?.name} active={a.id === selected?.id} />)}
            </div>
          )}
        </section>

        <div className="alerts-detail">
          {selected ? (
            <Detail alert={selected} camera={camById[selected.camera_id]} watch={selected.watch_id ? watches[selected.watch_id] : undefined}
              people={peopleMap} onChange={update} />
          ) : id && alerts.data ? (
            <div className="empty"><p>That alert is not in this list. It may have been dismissed.</p><Link to="/alerts">See all alerts</Link></div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
