import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, clipUrl } from "../api";
import { at, clock, dayLabel, epoch, plain } from "../format";
import { useLoad, useTitle } from "../hooks";
import brand from "../site/brand";
import type { IncidentDetail, IncidentStep } from "../types";
import { useCan } from "./session";
import { Page, PageHead, Problem, Section } from "./ui/Page";
import { SeverityTag } from "./ui/Severity";
import { isActive, sentence, statusWords } from "./words";

const VERDICT: Record<string, string> = {
  alert: "The AI check agreed it is real",
  approved: "The AI check agreed it is real",
  dismissed: "The AI check found nothing to worry about",
  awaiting_human: "The AI check asked a person to decide",
  unverified: "The AI check could not look at it",
};

function stepWords(step: IncidentStep, camera: string | undefined): string {
  switch (step.kind) {
    case "opened":
      return camera ? `First seen on ${camera}` : "First seen";
    case "repeated":
      return `Seen again, ${step.count} times in all`;
    case "telegram":
      return `Sent to ${step.group ?? "your group"} on Telegram`;
    case "reminder":
      return `Reminder sent to ${step.group ?? "your group"} on Telegram, as nobody had acknowledged it`;
    case "acknowledged":
      return `Acknowledged by ${step.by ?? "someone"}${step.via === "telegram" ? " on Telegram" : " in the app"}`;
    case "false_alarm":
      return `Marked a false alarm by ${step.by ?? "someone"}${step.via === "telegram" ? " on Telegram" : " in the app"}`;
    case "resolved":
      if (!step.by || step.by === "Resolved") return "Resolved";
      return step.by.startsWith("the ") ? `Closed by ${step.by}` : `Resolved. ${sentence(plain(step.by))}`;
    case "checked":
      return VERDICT[step.verdict] ?? "The AI check looked at it";
  }
}

function Steps({ inc, camera }: { inc: IncidentDetail; camera?: string }) {
  const day = dayLabel(epoch(inc.opened_at));
  return (
    <ol className="timeline" aria-label="What happened">
      {inc.timeline.map((step, i) => {
        const ts = epoch(step.at);
        return (
          <li key={i}>
            {step.at ? <time dateTime={step.at}>{dayLabel(ts) === day ? clock(ts) : at(ts)}</time> : <span className="when">Then</span>}
            <div>
              <p>{stepWords(step, camera)}</p>
              {step.kind === "checked" && step.why && <p className="small muted">{plain(step.why)}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export default function IncidentPage() {
  const { id = "" } = useParams();
  const supervisor = useCan("supervisor");
  const incident = useLoad(() => api.incident(id), [id], 10_000);
  const cameras = useLoad(() => api.cameras(), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [noClip, setNoClip] = useState(false);
  const inc = incident.data;
  const title = inc ? sentence(plain(inc.title || inc.reason)) : "Incident";
  useTitle(title);

  if (incident.loading && !inc) return <Page><p className="muted">Loading the incident</p></Page>;
  if (!inc) {
    return (
      <Page>
        <PageHead title="Incident not found" back={{ to: "/app", label: "Control room" }} />
        <Problem>{incident.error ?? "There is no incident at this address in your society."}</Problem>
      </Page>
    );
  }

  const camera = cameras.data?.find((c) => c.id === inc.camera_id);

  async function act(kind: "acknowledge" | "falseAlarm") {
    setBusy(true);
    setError(null);
    try {
      await (kind === "acknowledge" ? api.acknowledge(id) : api.falseAlarm(id));
      await incident.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const reason = plain(inc.reason);
  return (
    <Page>
      <PageHead
        back={{ to: "/app", label: "Control room" }}
        title={title}
        sub={
          <>
            <SeverityTag level={inc.severity} />
            <span>
              {camera ? <Link to={`/app/cameras/${camera.id}`}>{camera.name}</Link> : "A camera"}, {at(epoch(inc.opened_at))}
            </span>
            <span role="status">{statusWords(inc)}</span>
          </>
        }
      />
      <div className="incident">
        <div>
          <div className={`stage${inc.picture_url || playing ? "" : " stage-empty"}`}>
            {playing && inc.alert_id && !noClip ? (
              <video src={clipUrl(inc.alert_id)} controls autoPlay playsInline poster={inc.picture_url ?? undefined}
                onError={() => setNoClip(true)} aria-label="The clip of the moment" />
            ) : inc.picture_url ? (
              <img src={inc.picture_url} alt={`The moment ${brand.name} saw it on ${camera?.name ?? "the camera"}`} />
            ) : (
              <div className="stage-msg"><p>No picture was kept for this moment.</p></div>
            )}
          </div>
          {inc.alert_id && !playing && (
            <div><button type="button" className="btn" onClick={() => setPlaying(true)}>Play the clip</button></div>
          )}
          {noClip && <p className="small muted">No clip was saved for this moment.</p>}

          {isActive(inc) && (supervisor ? (
            <div className="act">
              {inc.status === "open" && (
                <button type="button" className="btn btn-primary" disabled={busy} onClick={() => act("acknowledge")}>Acknowledge</button>
              )}
              <button type="button" className="btn" disabled={busy} onClick={() => act("falseAlarm")}>False alarm</button>
            </div>
          ) : (
            <p className="hint">A supervisor can acknowledge this or mark it a false alarm.</p>
          ))}
          <Problem>{error}</Problem>
        </div>

        <div>
          <Section title="What happened" id="what-title">
            {reason && reason !== plain(inc.title) && <p>{sentence(reason)}</p>}
            {inc.graded_why && <p className="small muted">Why it is {inc.severity}: {plain(inc.graded_why)}</p>}
            <div className="card" style={{ padding: "14px 18px" }}>
              <Steps inc={inc} camera={camera?.name} />
            </div>
            {inc.count > 1 && isActive(inc) && (
              <p className="small muted">Repeats of this are counted here instead of sending a new alert each time.</p>
            )}
          </Section>
        </div>
      </div>
    </Page>
  );
}
