import type { Camera, Incident, Purposes, Severity } from "../types";

export const SEVERITY_WORDS: Record<Severity, string> = { critical: "Critical", warning: "Warning", info: "Info" };

export const SEVERITY_HELP: Record<Severity, string> = {
  critical: "Sent to your Telegram group at once, and posted there again while nobody acknowledges it.",
  warning: "Shown here, and on Telegram if your society turns that on.",
  info: "Kept as a note in the watch log and on the camera's history, and counted in the 9 AM daily summary on plans that have it.",
};

export const TIER_WORDS: Record<string, string> = {
  A: "Reliable",
  B: "Approximate",
  C: "The AI checks each time",
  X: "Not watched",
};

export type Tone = "" | "off" | "bad" | "down";

const HEALTH: Record<string, [string, Tone]> = {
  live: ["Live", ""],
  offline: ["Offline", "down"],
  covered: ["Covered", "bad"],
  moved: ["Moved", "bad"],
  blurred: ["Blurred", "bad"],
  never: ["Never connected", "down"],
};

// A camera's health in one word. The server's health judgement wins; until it sends one, the worker's state says.
export function healthOf(cam: Camera): { word: string; tone: Tone } {
  if (cam.kind === "file") return { word: "Recording", tone: "off" };
  if (!cam.enabled) return { word: cam.off_reason === "plan" ? "Switched off by the plan" : "Paused", tone: "off" };
  // its camera process could not start it: nothing is watched, whatever health was judged before
  if (cam.status.running && cam.status.source === "failed") return { word: "Offline", tone: "down" };
  const judged = cam.health ?? cam.status.health;
  if (judged && HEALTH[judged]) {
    const [word, tone] = HEALTH[judged];
    return { word, tone };
  }
  if (cam.kind === "webcam" && !cam.status.running) return { word: "Waiting for its device", tone: "off" };
  if (!cam.status.running) return { word: "Offline", tone: "down" };
  const src = cam.status.source;
  if (src === "refused") return { word: "Address refused", tone: "down" };
  if (src === "reconnecting" || src === "error") return { word: "Offline", tone: "down" };
  if (src === "starting" || src === "connecting") return { word: "Starting", tone: "off" };
  return { word: "Live", tone: "" };
}

// Why a camera shows what it shows, in a line under its name, when the word alone does not say what to do.
export function healthHelp(cam: Camera): string | null {
  if (cam.enabled && cam.status.source === "refused") {
    return "Its address points inside a private network, so this server won't read it. Add the camera through the bridge instead.";
  }
  return null;
}

// Indian plate colours in plain words: yellow is commercial, such as a cab.
export const PLATE_WORDS: Record<string, string> = {
  yellow: "Yellow plate, commercial or cab",
  white: "White plate, private",
  green: "Green plate, electric",
  black: "Black plate, rented",
};

export function purposeLabel(purposes: Purposes | null, key: string | undefined): string {
  if (!key) return "Other";
  return purposes?.[key]?.label ?? key[0]!.toUpperCase() + key.slice(1);
}

export function statusWords(inc: Pick<Incident, "status" | "ack_by">): string {
  if (inc.status === "acknowledged") return inc.ack_by ? `Acknowledged by ${inc.ack_by}` : "Acknowledged";
  if (inc.status === "false_alarm") return inc.ack_by ? `False alarm, marked by ${inc.ack_by}` : "False alarm";
  if (inc.status === "resolved") return "Resolved";
  return "Open";
}

export const isActive = (inc: Incident) => inc.status === "open" || inc.status === "acknowledged";

export function sentence(text: string): string {
  const t = text.trim();
  return t ? t[0]!.toUpperCase() + t.slice(1) : t;
}
