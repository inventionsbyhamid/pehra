export type Point = [number, number];

export type ZoneKind = "area" | "water" | "line";

export interface Zone {
  id?: string;
  name: string;
  polygon: Point[];
  kind?: ZoneKind;
}

export interface PipelineStats {
  frames?: number;
  frames_skipped?: number;
  detector_runs?: number;
  pose_runs?: number;
  face_runs?: number;
  embeds?: number;
  ms_per_frame?: number;
}

export type CameraKind = "stream" | "webcam" | "file" | "demo";
export type Health = "live" | "offline" | "covered" | "moved" | "blurred" | "never";

export interface Camera {
  id: string;
  name: string;
  kind: CameraKind;
  url: string | null;
  zones: Zone[];
  enabled: boolean;
  tuning: Record<string, unknown>;
  purpose?: string;
  last_frame_at?: number | null;
  health?: Health | null; // the camera's health, once the server reports it under its own key
  off_reason?: string | null; // "plan" when the society's plan switched it off
  status: {
    running: boolean;
    source: string;
    fps: number;
    loops: number;
    stats: PipelineStats;
    problems?: string[];
    health?: Health | null;
  };
  last_scan: ScanStats | null;
}

export interface LiveTrack {
  id: number;
  cls: string;
  box: [number, number, number, number];
  label: string | null;
  posture: string | null;
}

export interface Condition {
  signal: string;
  zones: string[];
  op: string;
  value: number | boolean | string | string[];
  weight: number;
  params: Record<string, unknown>;
}

export type Severity = "info" | "warning" | "critical";

export interface Watch {
  name: string;
  subject: Record<string, unknown>;
  zones: string[];
  conditions: Condition[];
  combine: "all" | "any" | "score";
  threshold: number;
  time_window: { start: string; end: string; tz?: string | null } | null;
  severity: Severity;
  verify: string;
  cooldown_s: number;
  repeat: boolean;
  expires_at: number | null;
  scope: "camera" | "site";
  excluded_cameras: string[];
  output: "alert" | "log";
}

export interface Coverage {
  clause: string;
  signal: string;
  tier: "A" | "B" | "C" | "X";
  caveat: string;
}

export interface Cannot {
  clause: string;
  reason: string;
  alternative: string;
}

export interface WatchRow {
  id: string;
  camera_id: string;
  text: string;
  watch: Watch;
  enabled: boolean;
  scope: "camera" | "site";
  excluded_cameras: string[];
  output: "alert" | "log";
  severity: Severity;
  coverage: Coverage[];
  setup: string[];
  cannot: Cannot[];
}

export interface Backtest {
  fires: number;
  examples: string[];
  visits: number;
  summary: string;
  cameras: number;
  events: number;
  assumed: string[];
  approximate: string[];
  skipped: string[];
  capped: boolean;
}

// what /api/cameras/{id}/watches/preview answers: the watch card
export interface WatchCard {
  watch: Watch | null;
  problem: string | null;
  coverage: Coverage[];
  setup: string[];
  cannot: Cannot[];
  backtest: Backtest | null;
}

export interface SavedWatch extends WatchCard {
  id?: string;
  saved: boolean;
}

export type Purposes = Record<string, { label: string; watches: string[] }>;

export interface StateExample {
  id: string;
  zone_id: string;
  state: string;
  url: string;
}

export type IncidentStatus = "open" | "acknowledged" | "false_alarm" | "resolved";

export interface Incident {
  id: string;
  camera_id: string | null;
  watch_id: string | null;
  severity: Severity;
  status: IncidentStatus;
  opened_at: string;
  last_at: string;
  count: number;
  reason: string;
  title: string;
  graded_why: string | null;
  ack_by: string | null;
  ack_at: string | null;
  closed_at: string | null;
  on_telegram: boolean;
  picture_url: string | null;
}

export type IncidentStep =
  | { kind: "opened"; at: string; text: string }
  | { kind: "repeated"; at: string; count: number }
  | { kind: "telegram" | "reminder"; at: string; group: string | null }
  | { kind: "acknowledged" | "false_alarm"; at: string; by: string | null; via: string | null }
  | { kind: "resolved"; at: string; by: string | null }
  | { kind: "checked"; at: null; verdict: string; why: string | null };

export interface IncidentDetail extends Incident {
  timeline: IncidentStep[];
  alert_id: string | null;
}

export type AlertStatus = "alert" | "unverified" | "dismissed" | "awaiting_human" | "approved" | "declined";

export interface TraceStep {
  step: number;
  tool: string;
  args: Record<string, unknown>;
  why: string;
  result: string;
  ms: number;
}

export interface Alert {
  id: string;
  camera_id: string;
  watch_id: string | null;
  ts: number;
  severity: Severity;
  status: AlertStatus;
  summary: string;
}

export interface AnswerItem {
  label: string;
  start_ts: number | null;
  end_ts: number | null;
  thumbnail_url: string | null;
  clip_url: string | null;
  event_id: string | null;
}

export interface Answer {
  text: string;
  items: AnswerItem[];
  confidence: "high" | "medium" | "low";
  trace: TraceStep[];
}

export interface EventRow {
  id: string;
  camera_id: string;
  ts: number;
  kind: string;
  track_id: number | null;
  cls: string | null;
  zone: string | null;
  data: Record<string, unknown>;
  keyframe_key: string | null;
  person_label: string | null;
  thumbnail_url: string | null;
}

export interface Visit {
  kind: "enter" | "exit";
  zone: string;
  ts: number;
  event_id: string;
}

export interface PeopleGroup {
  group_id: string;
  label: string | null;
  first_ts: number;
  last_ts: number;
  track_ids: number[];
  ambiguous: boolean;
  thumbnail_url: string | null;
  visits: Visit[];
  tracks?: { track_id: number; first_ts: number; last_ts: number }[];
}

export interface Timeline {
  events: EventRow[];
  people: PeopleGroup[];
}

export interface Person {
  person_id: string;
  name: string;
  consent_ts: number | null;
  thumbnail_url: string | null;
}

export interface ScanStats {
  duration_s: number;
  wall_s: number;
  x_realtime: number;
  frames_analyzed: number;
  detector_runs: number;
  workers: number;
  stages: Record<string, number>;
}

export interface Metrics {
  cameras: Camera[];
  ai_calls_today: number;
  ai_daily_limit: number;
  alerts: Partial<Record<AlertStatus, number>>;
  scans: ScanStats[];
}

export type Role = "viewer" | "supervisor" | "admin" | "owner";

export interface Society {
  id: string;
  name: string;
  timezone: string;
  plan?: string;
  demo?: boolean;
  expires_at?: string | null;
}

export interface Me {
  user: { id: string; email: string | null; name: string | null; demo?: boolean };
  workspace: Society | null;
  role: Role | null;
  workspaces?: (Society & { role: Role })[];
  timezone: string;
}

export interface Member {
  user_id: string;
  email: string | null;
  name: string | null;
  role: Role;
  you: boolean;
}

export interface PendingInvite {
  id: string;
  role: Role;
  expires_at: string;
}

export interface TelegramStatus {
  ready: boolean;
  connected: boolean;
  bot_username: string | null;
  message: string | null;
  groups: { id: string; title: string | null; connected_at: string | null }[];
  warnings: boolean;
}

export type LiveMessage =
  | { type: "frame"; ts: number; jpeg_b64: string; width: number; height: number; tracks: LiveTrack[] }
  | { type: "overlay"; ts: number; tracks: LiveTrack[] }
  | { type: "alert"; alert: Alert }
  | { type: "status"; camera_id: string; source_status: string; loops: number; fps: number; stats: PipelineStats; stopped?: boolean };
