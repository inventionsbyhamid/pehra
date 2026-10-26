import readers from "@data/readers";
import type { Camera, StateExample, WatchRow } from "../types";

// The setup tasks a watch card lists (agent/compiler.py _setup), read back so the camera page can offer the step
// and tick it off once done.
export type Task =
  | { kind: "zone" | "line"; name: string; text: string }
  | { kind: "pictures"; zone: string; state: string; not: boolean; text: string }
  | { kind: "reader"; reader: string; label: string; text: string }
  | { kind: "other"; text: string };

// "Turn on plate reading for this camera." points at the camera's plate reading setting (data/readers.json)
export function readerNamed(words: string): { id: string; label: string } | null {
  const hit = Object.entries(readers).find(([, r]) => r.label.toLowerCase() === words.trim().toLowerCase());
  return hit ? { id: hit[0], label: hit[1].label } : null;
}

// Staff recognition is for entry cameras only (the server refuses it elsewhere). The purposes come from
// data/readers.json when it lists them.
const ENTRY_PURPOSES = ["main gate", "service gate", "lobby"];

export function readerAllowed(id: string, purpose: string | undefined): string | null {
  const r = readers[id];
  if (!r || id !== "faces") return null;
  const allowed = r.purposes ?? ENTRY_PURPOSES;
  return allowed.includes(purpose ?? "other") ? null : r.refused ?? "Staff recognition is for entry cameras.";
}

export function parseTask(text: string): Task {
  let m = text.match(/^Draw a line called (.+?) where things cross/i);
  if (m) return { kind: "line", name: m[1]!, text };
  m = text.match(/^Draw a zone called (.+?) on (this camera|each camera it should run on)\.?$/i);
  if (m) return { kind: "zone", name: m[1]!, text };
  m = text.match(/^Turn on (.+?) (?:for this camera|on each camera it should run on)\.?$/i);
  const reader = m && readerNamed(m[1]!);
  if (reader) return { kind: "reader", reader: reader.id, label: reader.label, text };
  m = text.match(/^Save two pictures of the (.+?) when it is (not )?(.+?)(, by day and by night| by day| at night)\.?$/i);
  if (m) return { kind: "pictures", zone: m[1]!, state: m[3]!, not: !!m[2], text };
  return { kind: "other", text };
}

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

export function zoneNamed(cam: Camera, name: string, kind?: "line") {
  return cam.zones.find((z) => same(z.name, name) && (kind ? z.kind === "line" : z.kind !== "line"));
}

export function taskDone(task: Task, cam: Camera, examples: StateExample[]): boolean {
  if (task.kind === "zone") return !!zoneNamed(cam, task.name);
  if (task.kind === "line") return !!zoneNamed(cam, task.name, "line");
  if (task.kind === "reader") return !!cam.readers?.includes(task.reader);
  if (task.kind === "pictures") {
    const zone = zoneNamed(cam, task.zone);
    if (!zone) return false;
    const mine = examples.filter((e) => e.zone_id === zone.id);
    return task.not ? mine.some((e) => !same(e.state, task.state)) : mine.filter((e) => same(e.state, task.state)).length >= 2;
  }
  return false;
}

// The tasks a camera's page shows for a watch: its own camera's, and every site watch's.
export function tasksFor(row: WatchRow, cam: Camera): Task[] {
  if (row.scope !== "site" && row.camera_id !== cam.id) return [];
  return (row.setup ?? []).map(parseTask);
}

// A watch saved from a preset keeps the preset's key as its text; its watch name reads better.
export function watchWords(row: Pick<WatchRow, "text" | "watch">): string {
  return row.text && /\s/.test(row.text.trim()) ? row.text : row.watch?.name || row.text;
}
