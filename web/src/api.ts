import brand from "./site/brand";
import type {
  Answer, Camera, Incident, IncidentDetail, Me, Member, Metrics, PendingInvite, Person, Purposes, Role, SavedWatch,
  Society, StateExample, TelegramStatus, Timeline, Watch, WatchCard, WatchRow, Zone,
} from "./types";

export type { Me, Society } from "./types";

export interface Invite {
  society: string;
  invited_by: string | null;
  role: string;
  expires_at: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let onSignedOut: () => void = () => {};
export function whenSignedOut(fn: () => void) {
  onSignedOut = fn;
}

function messageFrom(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (status === 422) return "Some details are missing or not in the right shape. Check the form and try again.";
  if (status >= 500) return "The server hit a problem. Try again in a moment.";
  return `The request did not work (status ${status}). Try again.`;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin" };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "content-type": "application/json" };
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, `${brand.name} can't be reached. Check your connection and try again.`);
  }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    if (res.status === 401) onSignedOut();
    throw new ApiError(res.status, messageFrom(res.status, data));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});
const del = <T>(path: string) => request<T>("DELETE", path);

// the judges' demo (api/demo.py)
export interface DemoInfo {
  sandbox_id: string;
  expires_at: string;
  ended: boolean;
  clips: { camera: string; source: string }[];
  own_cameras: number;
  own_cameras_allowed: number;
}

// the message a society's Telegram group gets for an incident, as alerts/telegram.py makes it
export interface TelegramMessage {
  caption: string;
  buttons: { text: string; url: string | null }[][];
  photo: string | null;
  goes_to_telegram: boolean;
  sent: boolean;
  connected: boolean;
}

export interface DeviceLink {
  token: string;
  url: string;
  qr_url: string;
}

export interface WatchBody {
  text: string;
  watch?: Watch;
  coverage?: WatchCard["coverage"];
  setup?: string[];
  cannot?: WatchCard["cannot"];
  scope?: "camera" | "site";
  excluded_cameras?: string[];
}

export const api = {
  me: () => get<Me>("/api/me"),
  providers: () => get<{ providers: { name: string; label: string }[] }>("/api/auth/providers"),
  createSociety: (name: string, timezone: string) =>
    post<{ workspace: Society; role: string }>("/api/workspaces", { name, timezone }),
  switchSociety: (workspace_id: string) => post<{ workspace: Society; role: Role }>("/api/me/workspace", { workspace_id }),
  invite: (token: string) => get<Invite>(`/api/invites/${encodeURIComponent(token)}`),
  acceptInvite: (token: string) => post<{ workspace: Society; role: string }>("/api/invites/accept", { token }),
  logout: () => fetch("/auth/logout", { method: "POST", credentials: "same-origin" }),

  members: () => get<{ members: Member[]; invites: PendingInvite[] }>("/api/members"),
  changeRole: (userId: string, role: Role) => request<{ role: Role }>("PATCH", `/api/members/${userId}`, { role }),
  removeMember: (userId: string) => del<void>(`/api/members/${userId}`),
  makeInvite: (role: Role) => post<{ invite: PendingInvite; url: string }>("/api/invites", { role }),
  revokeInvite: (id: string) => del<void>(`/api/invites/${id}`),

  cameras: () => get<Camera[]>("/api/cameras"),
  camera: (id: string) => get<Camera>(`/api/cameras/${id}`),
  // notice: why the plan leaves a new camera unwatched (it is added, paused)
  addCamera: (body: { name: string; kind: "stream" | "webcam"; url?: string; purpose?: string }) =>
    post<Camera & { notice?: string | null }>("/api/cameras", body),
  patchCamera: (id: string, patch: { name?: string; purpose?: string; zones?: Zone[]; enabled?: boolean }) =>
    request<Camera & { disabled_watches?: string[] }>("PATCH", `/api/cameras/${id}`, patch),
  deleteCamera: (id: string) => del<{ ok: boolean }>(`/api/cameras/${id}`),
  examples: (cameraId: string) => get<StateExample[]>(`/api/cameras/${cameraId}/examples`),
  addExample: (cameraId: string, zoneId: string, state: string) => {
    const form = new FormData();
    form.append("state", state);
    return post<StateExample>(`/api/cameras/${cameraId}/zones/${zoneId}/examples`, form);
  },

  purposes: () => get<Purposes>("/api/purposes"),
  watches: (cameraId: string) => get<WatchRow[]>(`/api/cameras/${cameraId}/watches`),
  previewWatch: (cameraId: string, body: WatchBody) =>
    post<WatchCard>(`/api/cameras/${cameraId}/watches/preview`, body),
  saveWatch: (cameraId: string, body: WatchBody) => post<SavedWatch>(`/api/cameras/${cameraId}/watches`, body),
  toggleWatch: (id: string, enabled: boolean) => request<WatchRow>("PATCH", `/api/watches/${id}`, { enabled }),

  incidents: (filter: { status?: string; camera_id?: string } = {}) => {
    const q = new URLSearchParams(Object.entries(filter).filter(([, v]) => v) as [string, string][]);
    return get<Incident[]>(`/api/incidents?${q}`);
  },
  incident: (id: string) => get<IncidentDetail>(`/api/incidents/${id}`),
  acknowledge: (id: string) => post<Incident & { changed: boolean }>(`/api/incidents/${id}/acknowledge`),
  falseAlarm: (id: string) => post<Incident & { changed: boolean }>(`/api/incidents/${id}/false_alarm`),
  precision: () => get<Record<string, { acknowledged?: number; false_alarm?: number; precision?: number | null }>>(
    "/api/incidents/precision"),

  telegram: () => get<TelegramStatus>("/api/telegram"),
  telegramConnect: () =>
    post<{ code: string; command: string; expires_at: string; bot_username: string | null }>("/api/telegram/connect"),
  telegramWarnings: (on: boolean) => post<{ warnings: boolean }>("/api/telegram/warnings", { on }),
  telegramRemove: (linkId: string) => del<{ ok: boolean }>(`/api/telegram/groups/${linkId}`),

  ask: (question: string, cameraId?: string) =>
    post<Answer>("/api/ask", { question, camera_id: cameraId || null }),
  timeline: (cameraId: string) => get<Timeline>(`/api/cameras/${cameraId}/timeline`),

  people: () => get<Person[]>("/api/people"),
  enrol: (form: FormData) => post<{ person_id: string; name: string }>("/api/people", form),
  forget: (id: string) => del<{ ok: boolean }>(`/api/people/${id}`),

  metrics: () => get<Metrics>("/api/metrics"),

  deviceLink: (cameraId: string) => post<DeviceLink>(`/api/cameras/${cameraId}/device-token`),
  demo: () => get<DemoInfo>("/api/demo"),
  demoLogin: (signOut = false) => post<{ next: string }>("/api/demo/login", { sign_out: signOut }),
  telegramPreview: (incidentId: string) => get<TelegramMessage>(`/api/incidents/${incidentId}/telegram`),
};

export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}

export const frameUrl = (cameraId: string, bust = "") => `/api/cameras/${cameraId}/frame${bust ? `?v=${bust}` : ""}`;
export const clipUrl = (alertId: string) => `/api/alerts/${alertId}/clip`;
