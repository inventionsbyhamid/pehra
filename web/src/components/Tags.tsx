import type { AlertStatus, Camera, Severity } from "../types";

function Shape({ kind }: { kind: "solid" | "triangle" | "ring" | "check" | "dash" }) {
  return (
    <svg viewBox="0 0 10 10" aria-hidden="true">
      {kind === "solid" && <circle cx="5" cy="5" r="4.5" fill="currentColor" />}
      {kind === "triangle" && <path d="M5 0.6 9.6 9.2H0.4Z" fill="currentColor" />}
      {kind === "ring" && <circle cx="5" cy="5" r="3.6" fill="none" stroke="currentColor" strokeWidth="1.8" />}
      {kind === "check" && <path d="M1.2 5.2 4 8l4.8-6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />}
      {kind === "dash" && <path d="M1.5 5h7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />}
    </svg>
  );
}

const SEVERITY: Record<Severity, [string, string, "solid" | "triangle" | "ring"]> = {
  critical: ["Urgent", "tag-critical", "solid"],
  warning: ["Warning", "tag-warning", "triangle"],
  info: ["For your notes", "", "ring"],
};

export function SeverityTag({ severity }: { severity: Severity }) {
  const [label, cls, shape] = SEVERITY[severity] ?? SEVERITY.info;
  return <span className={`tag ${cls}`}><Shape kind={shape} />{label}</span>;
}

export const STATUS_WORDS: Record<AlertStatus, string> = {
  alert: "Checked by AI",
  unverified: "Not checked by AI",
  awaiting_human: "Needs your decision",
  approved: "Approved",
  declined: "Declined",
  dismissed: "Dismissed by AI",
};

export function AlertStatusTag({ status }: { status: AlertStatus }) {
  const cls = {
    alert: "tag-good", unverified: "tag-warning tag-outline", awaiting_human: "tag-accent",
    approved: "tag-good", declined: "", dismissed: "",
  }[status];
  const shape = status === "alert" || status === "approved" ? "check" : status === "unverified" ? "ring" : status === "awaiting_human" ? "solid" : "dash";
  return <span className={`tag ${cls}`}><Shape kind={shape} />{STATUS_WORDS[status] ?? status}</span>;
}

export function cameraState(cam: Camera): { label: string; tone: "" | "off" | "warn" } {
  const src = cam.status.source;
  if (cam.kind === "file") return { label: "Recording", tone: "off" };
  if (!cam.enabled) return { label: "Paused", tone: "off" };
  if (cam.kind === "webcam" && !cam.status.running) return { label: "Waiting for a webcam", tone: "off" };
  if (!cam.status.running) return { label: "Stopped", tone: "off" };
  if (src === "reconnecting" || src === "error") return { label: "Reconnecting", tone: "warn" };
  if (src === "starting" || src === "connecting") return { label: "Starting", tone: "warn" };
  if (src === "ended") return { label: "Ended", tone: "off" };
  if (cam.kind === "demo" && cam.status.loops >= 1) return { label: "Live when open", tone: "" };
  return { label: "Live", tone: "" };
}

export const KIND_WORDS: Record<Camera["kind"], string> = {
  stream: "Stream",
  webcam: "Browser webcam",
  file: "Uploaded video",
  demo: "Demo camera",
};
