import type { Condition, Watch } from "./types";

// A compiled watch read back as a sentence. Slots are the parts the AI filled in.
export type Part = { text: string; slot?: string };

const GROUPS: [string, string[]][] = [
  ["a vehicle", ["car", "truck", "bus", "motorcycle", "bicycle"]],
  ["a vehicle", ["car", "truck", "bus", "motorcycle"]],
  ["a bag", ["backpack", "handbag", "suitcase"]],
  ["a pet", ["dog", "cat"]],
  ["an animal", ["dog", "cat", "bird", "horse", "sheep", "cow"]],
];

function article(word: string) {
  return /^[aeiou]/i.test(word) ? `an ${word}` : `a ${word}`;
}

export function classWords(classes: string[]): string {
  if (!classes.length || (classes.length === 1 && classes[0] === "person")) return "someone";
  const set = new Set(classes);
  for (const [word, members] of GROUPS) {
    if (members.length === set.size && members.every((m) => set.has(m))) return word;
  }
  const words = classes.map((c) => (c === "person" ? "a person" : article(c)));
  return words.length === 1 ? words[0]! : `${words.slice(0, -1).join(", ")} or ${words.at(-1)}`;
}

function list(zones: string[], joiner = "or"): string {
  if (zones.length <= 1) return zones[0] ?? "";
  return `${zones.slice(0, -1).join(", ")} ${joiner} ${zones.at(-1)}`;
}

function minutes(s: unknown, fallback: number): string {
  const n = typeof s === "number" ? s : fallback;
  return n >= 120 ? `${Math.round(n / 60)} min` : `${Math.round(n)} s`;
}

function op(c: Condition): string {
  if (c.op === "<=") return "or less";
  if (c.op === "==") return "exactly";
  return "or more";
}

function conditionParts(c: Condition): Part[] {
  const where = c.zones.length ? list(c.zones) : "";
  const at = (verb: string, prep = "into"): Part[] =>
    where ? [{ text: verb, slot: "does" }, { text: prep }, { text: where, slot: "where" }] : [{ text: verb, slot: "does" }];
  const v = c.value;
  switch (c.signal) {
    case "present":
      return where ? [{ text: "is seen", slot: "does" }, { text: "in" }, { text: where, slot: "where" }] : [{ text: "is seen", slot: "does" }];
    case "entered":
      return at("comes", "into");
    case "exited":
      return where ? [{ text: "goes out of", slot: "does" }, { text: where, slot: "where" }] : [{ text: "leaves", slot: "does" }];
    case "in_zone":
      return at("is", "in");
    case "dwell_s":
      return [
        ...(where ? [{ text: "stays in" }, { text: where, slot: "where" }] : [{ text: "stays" }]),
        { text: `for ${v} s ${op(c)}`, slot: "how long" },
      ];
    case "speed":
      return [{ text: `moves at ${v} body heights a second ${op(c)}`, slot: "does" }];
    case "posture":
      if (v === "fallen") return [{ text: "has fallen and stays down", slot: "does" }];
      if (v === "falling") return [{ text: "starts to fall", slot: "does" }];
      return [{ text: `is ${v}`, slot: "does" }];
    case "count":
      return [
        { text: `${v} ${op(c)}`, slot: "how many" },
        { text: where ? "are in" : "are" },
        { text: where || "in view", slot: "where" },
        { text: "at once" },
      ];
    case "zones_visited":
      return [
        { text: `tries ${v} ${op(c)} of`, slot: "does" },
        { text: list(c.zones, "and") || "the zones", slot: "where" },
        { text: `within ${minutes(c.params.window_s, 300)}`, slot: "how long" },
      ];
    case "hands_in_zone_s":
      return [{ text: "keeps their hands at", slot: "does" }, { text: where || "a zone", slot: "where" },
        { text: `for ${v} s`, slot: "how long" }];
    case "climbing":
      return [{ text: "climbs", slot: "does" }, ...(where ? [{ text: where, slot: "where" }] : [])];
    case "returns":
      return [{ text: `keeps coming back, ${v} times`, slot: "does" },
        { text: `within ${minutes(c.params.window_s, 1800)}`, slot: "how long" }];
    case "looks_like": {
      const look = String(c.params.text ?? (typeof v === "string" ? v : "") ?? "");
      const not = c.params.not_text ? String(c.params.not_text) : "";
      return [{ text: "looks like" }, { text: look || "the description", slot: "looks like" },
        ...(not ? [{ text: "rather than" }, { text: not, slot: "instead of" }] : [])];
    }
    case "zone_looks_like":
      return [{ text: where || "the view", slot: "where" }, { text: "looks like" },
        { text: String(c.params.text ?? "something"), slot: "looks like" },
        ...(c.params.hold_s ? [{ text: `for ${minutes(c.params.hold_s, 60)}`, slot: "how long" }] : [])];
    default:
      return [{ text: `${c.signal.replace(/_/g, " ")} ${c.op} ${String(v)}`, slot: "does" }];
  }
}

const SEVERITY: Record<Watch["severity"], string> = {
  info: "note it",
  warning: "warn me",
  critical: "treat it as urgent",
};

export function subjectParts(w: Watch, people: Record<string, string>): Part[] {
  const s = w.subject;
  const parts: Part[] = [];
  if (s.same_as) parts.push({ text: "the person from that sighting", slot: "who" });
  else if (s.who && s.who !== "anyone") {
    const who = s.who === "unknown" ? "someone not enrolled" : s.who === "known" ? "someone enrolled" : people[s.who] ?? "an enrolled person";
    parts.push({ text: who, slot: "who" });
  } else parts.push({ text: classWords(s.classes), slot: "who" });
  if (s.looks_like) parts.push({ text: "who looks like" }, { text: s.looks_like, slot: "looks like" });
  return parts;
}

export function watchSentence(w: Watch, people: Record<string, string> = {}): Part[] {
  const scene = w.conditions.every((c) => c.signal === "count" || c.signal === "zone_looks_like");
  const parts: Part[] = [{ text: "Tell me when" }];
  if (!scene) parts.push(...subjectParts(w, people));
  const joiner = w.combine === "any" ? "or" : "and";
  if (w.combine === "score" && w.conditions.length > 1) {
    parts.push({ text: "does enough of these:" });
    w.conditions.forEach((c, i) => {
      parts.push(...conditionParts(c));
      if (i < w.conditions.length - 1) parts.push({ text: ";" });
    });
  } else {
    w.conditions.forEach((c, i) => {
      if (i > 0) parts.push({ text: joiner });
      parts.push(...conditionParts(c));
    });
  }
  if (w.zones.length && !w.conditions.some((c) => c.zones.length)) parts.push({ text: "in" }, { text: list(w.zones), slot: "where" });
  if (w.time_window) parts.push({ text: "between" }, { text: `${w.time_window.start} and ${w.time_window.end}`, slot: "when" });
  else parts.push({ text: "at any time", slot: "when" });
  parts.push({ text: "and" }, { text: SEVERITY[w.severity], slot: "how serious" });
  return parts;
}
