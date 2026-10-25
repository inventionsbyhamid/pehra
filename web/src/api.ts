import type {
  AddWatchResult, Alert, Answer, Camera, Metrics, Person, Preset, Scan, SearchResponse, Timeline, WatchRow, Zone,
} from "./types";

export interface Society {
  id: string;
  name: string;
  timezone: string;
}

export interface Me {
  user: { id: string; email: string | null; name: string | null };
  workspace: Society | null;
  role: string | null;
  timezone: string;
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
    throw new ApiError(0, "Watchful can't be reached. Check your connection and try again.");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    if (res.status === 401) onSignedOut();
    throw new ApiError(res.status, messageFrom(res.status, data));
  }
  return res.json() as Promise<T>;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {});

export const api = {
  me: () => get<Me>("/api/me"),
  providers: () => get<{ providers: { name: string; label: string }[] }>("/api/auth/providers"),
  createSociety: (name: string, timezone: string) =>
    post<{ workspace: Society; role: string }>("/api/workspaces", { name, timezone }),
  acceptInvite: (token: string) => post<{ workspace: Society; role: string }>("/api/invites/accept", { token }),
  logout: () => fetch("/auth/logout", { method: "POST", credentials: "same-origin" }),

  cameras: () => get<Camera[]>("/api/cameras"),
  camera: (id: string) => get<Camera>(`/api/cameras/${id}`),
  deleteCamera: (id: string) => request<{ ok: boolean }>("DELETE", `/api/cameras/${id}`),
  addCamera: (name: string, kind: "stream" | "webcam", url?: string) =>
    post<Camera>("/api/cameras", { name, kind, url }),
  patchCamera: (id: string, patch: { name?: string; zones?: Zone[]; enabled?: boolean }) =>
    request<Camera>("PATCH", `/api/cameras/${id}`, patch),
  rescan: (id: string) => post<{ scan_id: string }>(`/api/cameras/${id}/scan`),
  scan: (id: string) => get<Scan>(`/api/scans/${id}`),

  presets: () => get<Preset[]>("/api/presets"),
  watches: (cameraId: string) => get<WatchRow[]>(`/api/cameras/${cameraId}/watches`),
  addWatch: (cameraId: string, body: { text: string } | { preset: string; fill: Record<string, unknown> }) =>
    post<AddWatchResult>(`/api/cameras/${cameraId}/watches`, body),
  toggleWatch: (id: string, enabled: boolean) => request<WatchRow>("PATCH", `/api/watches/${id}`, { enabled }),

  alerts: (cameraId?: string, includeDismissed = false) => {
    const q = new URLSearchParams();
    if (cameraId) q.set("camera_id", cameraId);
    if (includeDismissed) q.set("include_dismissed", "true");
    return get<Alert[]>(`/api/alerts?${q}`);
  },
  feedback: (id: string, value: "up" | "down") => post<{ ok: boolean }>(`/api/alerts/${id}/feedback`, { value }),
  approve: (id: string) => post<{ notified: boolean }>(`/api/alerts/${id}/approve`),
  decline: (id: string) => post<{ ok: boolean }>(`/api/alerts/${id}/decline`),

  ask: (question: string, cameraId?: string) =>
    post<Answer>("/api/ask", { question, camera_id: cameraId || null }),
  timeline: (cameraId: string) => get<Timeline>(`/api/cameras/${cameraId}/timeline`),

  search: (body: { text: string; camera_id?: string; since?: number; until?: number } | FormData) =>
    post<SearchResponse>("/api/search", body),
  lookout: (body: { camera_ids: string[]; person_id?: string; description?: string; event_id?: string; alert_id?: string }) =>
    post<{ watch_ids: string[] }>("/api/lookouts", body),

  people: () => get<Person[]>("/api/people"),
  enrol: (form: FormData) => post<{ person_id: string; name: string }>("/api/people", form),
  forget: (id: string) => request<{ ok: boolean }>("DELETE", `/api/people/${id}`),

  metrics: () => get<Metrics>("/api/metrics"),
};

export function uploadVideo(file: File, name: string, onProgress: (fraction: number) => void,
  signal?: AbortSignal): Promise<{ camera_id: string; scan_id: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("file", file);
    form.append("name", name);
    xhr.open("POST", "/api/uploads");
    xhr.withCredentials = true;
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response);
      else {
        if (xhr.status === 401) onSignedOut();
        reject(new ApiError(xhr.status, messageFrom(xhr.status, xhr.response)));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "The upload stopped. Check your connection and try again."));
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled."));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(form);
  });
}

export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}

export const frameUrl = (cameraId: string, bust = "") => `/api/cameras/${cameraId}/frame${bust ? `?v=${bust}` : ""}`;
export const clipUrl = (alertId: string) => `/api/alerts/${alertId}/clip`;
