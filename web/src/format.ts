// Times show in the cameras' time zone when the server tells us, so they match the AI's answers.
let timeZone: string | undefined;
let clock: Intl.DateTimeFormat, clockShort: Intl.DateTimeFormat, dayName: Intl.DateTimeFormat;
let dayMonth: Intl.DateTimeFormat, dayKey: Intl.DateTimeFormat;

export function setTimeZone(tz?: string) {
  try {
    if (tz) new Intl.DateTimeFormat(undefined, { timeZone: tz });
  } catch {
    tz = undefined;
  }
  timeZone = tz;
  clock = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone });
  clockShort = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false, timeZone });
  dayName = new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone });
  dayMonth = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", timeZone });
  dayKey = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone });
}
setTimeZone();

export function dayLabel(ts: number): string {
  const d = new Date(ts * 1000);
  const now = new Date();
  if (dayKey.format(d) === dayKey.format(now)) return "Today";
  if (dayKey.format(d) === dayKey.format(new Date(now.getTime() - 86400_000))) return "Yesterday";
  if (now.getTime() - d.getTime() < 6 * 86400_000) return dayName.format(d);
  return dayMonth.format(d);
}

export function timeOf(ts: number, seconds = true): string {
  return (seconds ? clock : clockShort).format(new Date(ts * 1000));
}

export function when(ts: number): string {
  const day = dayLabel(ts);
  return day === "Today" ? timeOf(ts) : `${day} ${timeOf(ts)}`;
}

export function ago(ts: number): string {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return dayLabel(ts);
}

export function span(start: number | null, end: number | null): string {
  if (start == null) return "";
  if (end == null || Math.abs(end - start) < 1) return timeOf(start);
  return `${timeOf(start)} to ${timeOf(end)}`;
}

export function clockDuration(seconds: number): string {
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`;
}

export function seconds(s: number): string {
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 90) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

export function times(x: number): string {
  return `${x >= 10 ? Math.round(x) : x.toFixed(1)}x`;
}

export function count(n: number | undefined): string {
  return (n ?? 0).toLocaleString();
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

// AI written text can carry dashes; the product voice uses none.
export function plain(text: string | null | undefined): string {
  if (!text) return "";
  return text
    .replace(/\bts\s+(?=1[6-9]\d{8})/g, "")
    .replace(/\b1[6-9]\d{8}(?:\.\d+)?\b/g, (m) => timeOf(Number(m)))
    .replace(/\s*[—–]\s*/g, ", ")
    .replace(/(\p{L})-(?=\p{L})/gu, "$1 ")
    .replace(/,\s*,/g, ",")
    .trim();
}

export function scanLine(stats: { duration_s: number; wall_s: number; x_realtime: number }): string {
  return `Scanned ${clockDuration(stats.duration_s)} of video in ${stats.wall_s.toFixed(1)} s (${times(stats.x_realtime)} real time)`;
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
}
