export type Point = [number, number];

export interface Zone {
  id?: string;
  name: string;
  polygon: Point[];
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

export interface Camera {
  id: string;
  name: string;
  kind: CameraKind;
  url: string | null;
  zones: Zone[];
  enabled: boolean;
  tuning: Record<string, unknown>;
  status: { running: boolean; source: string; fps: number; loops: number; stats: PipelineStats };
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
  op: ">=" | "<=" | "==" | "is";
  value: number | boolean | string;
  weight: number;
  params: Record<string, unknown>;
}

export interface Watch {
  name: string;
  subject: {
    classes: string[];
    who: string;
    looks_like: string | null;
    same_as: { event_id: string | null; expires_at: number | null } | null;
  };
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
}

export interface WatchRow {
  id: string;
  camera_id: string;
  text: string;
  watch: Watch;
  enabled: boolean;
}

export interface AddWatchResult {
  id?: string;
  watch: Watch | null;
  problem: string | null;
}

export interface Preset {
  preset: string;
  description: string;
  watch: Record<string, unknown>;
}

export type Severity = "info" | "warning" | "critical";
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
  clip_key: string | null;
  keyframe_key: string | null;
  trace: TraceStep[];
  feedback: "up" | "down" | null;
  fingerprint: string;
  track_id: number | null;
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

export interface SearchQuery {
  looks_like: string | null;
  not_like?: string | null;
  classes?: string[];
  camera_id: string | null;
  since: number | null;
  until: number | null;
  zones?: string[];
}

export interface SearchResult {
  kind: "scene" | "object";
  camera_id: string;
  ts: number;
  start_ts: number | null;
  end_ts: number | null;
  thumbnail_url: string | null;
  score: number;
  confirmed: boolean;
  method: "embedding" | "face";
  event_id: string | null;
  track_id: number | null;
  cls: string | null;
}

export interface SearchResponse {
  query: SearchQuery | null;
  problem: string | null;
  results: SearchResult[];
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

export interface Scan {
  state: "scanning" | "checking" | "done" | "failed";
  progress: number;
  stats: ScanStats | null;
  alert_ids: string[];
  camera_id: string;
  error: string | null;
}

export interface Metrics {
  cameras: Camera[];
  ai_calls_today: number;
  ai_daily_limit: number;
  alerts: Partial<Record<AlertStatus, number>>;
  scans: ScanStats[];
}

export type LiveMessage =
  | { type: "frame"; ts: number; jpeg_b64: string; width: number; height: number; tracks: LiveTrack[] }
  | { type: "overlay"; ts: number; tracks: LiveTrack[] }
  | { type: "alert"; alert: Alert }
  | { type: "status"; camera_id: string; source_status: string; loops: number; fps: number; stats: PipelineStats; stopped?: boolean };
