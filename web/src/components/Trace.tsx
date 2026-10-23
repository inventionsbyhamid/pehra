import type { TraceStep } from "../types";
import { plain, timeOf } from "../format";

const TOOL_WORDS: Record<string, string> = {
  get_frames: "Looked at more frames",
  crop_zoom: "Zoomed in",
  enhance: "Cleaned up a picture",
  motion_trail: "Traced the path",
  flow: "Measured movement",
  pose_history: "Checked the posture over time",
  raise_alert: "Raised the alert",
  dismiss: "Dismissed it",
  ask_human: "Asked a person to decide",
  events: "Read the event log",
  people: "Grouped the people seen",
  keyframes: "Looked at pictures",
  answer: "Answered",
};

const ARG_WORDS: Record<string, string> = {
  t0: "from", t1: "to", ts: "at", from_s: "from", to_s: "to", at_s: "at", n: "frames", track_id: "track", since: "since", until: "until",
  seconds: "seconds", image: "picture", kind: "kind", zone: "zone", cls: "class", kinds: "kinds", event_ids: "events",
};

function value(v: unknown): string {
  if (typeof v === "number") return v > 1.6e9 ? timeOf(v) : String(Math.round(v * 10) / 10);
  if (typeof v === "string" && /^\d{10}(\.\d+)?$/.test(v)) return timeOf(Number(v));
  if (Array.isArray(v)) return v.map(value).join(", ");
  return String(v);
}

// tools take seconds relative to the flagged moment
function relative(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (Math.abs(n) < 0.05) return "the moment";
  return `${Math.round(Math.abs(n) * 10) / 10} s ${n < 0 ? "before" : "after"}`;
}

function args(a: Record<string, unknown>): string {
  const entries = Object.entries(a).filter(([k]) => !["summary", "reason", "question", "text", "items", "confidence"].includes(k));
  return entries
    .map(([k, v]) => `${ARG_WORDS[k] ?? k.replace(/_/g, " ")} ${["from_s", "to_s", "at_s"].includes(k) ? relative(v) : value(v)}`)
    .join(", ");
}

export function Trace({ steps }: { steps: TraceStep[] }) {
  if (!steps.length) return <p className="muted small">No AI steps were recorded for this one.</p>;
  return (
    <ol className="trace">
      {steps.map((s) => {
        const detail = args(s.args);
        const said = (s.args.summary ?? s.args.reason ?? s.args.question) as string | undefined;
        return (
          <li key={s.step}>
            <div className="trace-head">
              <span className="trace-tool">{TOOL_WORDS[s.tool] ?? s.tool.replace(/_/g, " ")}</span>
              <span className="muted xsmall num">{(s.ms / 1000).toFixed(1)} s</span>
            </div>
            {s.why && <p className="trace-why">{plain(s.why)}</p>}
            {said && <p className="trace-said">{plain(said)}</p>}
            {detail && <p className="trace-args xsmall muted">{detail}</p>}
            {s.result && (
              <details className="trace-result">
                <summary className="xsmall">What it got back</summary>
                <pre>{s.result.replace(/\|\s*-\s*\|/g, "| none |")}</pre>
              </details>
            )}
          </li>
        );
      })}
    </ol>
  );
}
