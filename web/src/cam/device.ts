// This device as a camera: the browser's camera, faces covered when the camera keeps none (faceBlur.ts), JPEG frames
// to /ws/cam/{token} at the rate the server says. Reconnects with growing waits; stops for good when the link was
// replaced, the camera deleted or turned off, or another device took it over.
import { wsUrl } from "../api";
import { FaceBlur, type FaceSettings } from "./faceBlur";

export type Phase = "ready" | "starting" | "live" | "reconnecting" | "taken" | "off" | "gone" | "error";
export type DeviceInfo = {
  camera: string;
  society: string;
  enabled: boolean;
  blur: boolean;
  retention_days: number;
  send: { fps: number; width: number; quality: number };
  faces: FaceSettings;
};
export type DeviceState = { phase: Phase; problem?: string; sent: number; covered: number; blurReady: boolean };

const RETRY_MS = [1000, 2000, 4000, 8000, 15000];
const BUSY_WAIT_MS = 60_000;
const MAX_BUFFERED = 1_000_000;
const ENDS: Record<number, Phase> = { 4400: "gone", 4404: "gone", 4410: "gone", 4409: "taken", 4412: "off" };

export const PROBLEMS = {
  noCamera: "This browser can't use a camera here. Open the link over https in a current Chrome, Safari or Firefox.",
  blocked: "Camera access was blocked. Allow the camera for this site in the browser settings, then try again.",
  missing: "No camera was found on this device.",
  busy: "The camera could not start. Close other apps that use it, then try again.",
  blur: "Face blur could not start on this device, so nothing was sent. Try again, or use a current Chrome, Safari or Firefox.",
  ended: "The camera stopped. Start it again.",
};

export class DeviceCamera {
  private stream: MediaStream | null = null;
  private video = document.createElement("video");
  private ws: WebSocket | null = null;
  private blur: FaceBlur | null = null;
  private mustBlur: boolean;
  private send: DeviceInfo["send"];
  private timer = 0;
  private retry = 0;
  private retryTimer = 0;
  private busy = false;
  private stopped = false;
  private wake: { release: () => Promise<void> } | null = null;
  private state: DeviceState = { phase: "ready", sent: 0, covered: 0, blurReady: false };

  constructor(private token: string, private info: DeviceInfo, private canvas: HTMLCanvasElement,
    private onChange: (s: DeviceState) => void) {
    this.mustBlur = info.blur;
    this.send = { ...info.send };
  }

  private set(patch: Partial<DeviceState>) {
    this.state = { ...this.state, ...patch };
    this.onChange(this.state);
  }

  async start() {
    this.set({ phase: "starting", problem: undefined, sent: 0, covered: 0 });
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) return this.fail(PROBLEMS.noCamera);
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { width: { ideal: this.send.width }, facingMode: { ideal: "environment" } },
      });
    } catch (e) {
      const name = (e as DOMException).name;
      return this.fail(name === "NotAllowedError" || name === "SecurityError" ? PROBLEMS.blocked
        : name === "NotFoundError" || name === "OverconstrainedError" ? PROBLEMS.missing : PROBLEMS.busy);
    }
    if (this.stopped) return this.release();
    this.stream.getVideoTracks()[0]?.addEventListener("ended", () => !this.stopped && this.fail(PROBLEMS.ended));
    const v = this.video;
    v.muted = true;
    v.playsInline = true;
    v.srcObject = this.stream;
    await v.play().catch(() => {});
    if (this.mustBlur && !(await this.loadBlur())) return;
    if (this.stopped) return this.release();
    void this.keepAwake();
    this.connect();
    this.timer = window.setInterval(this.tick, 1000 / this.send.fps);
  }

  stop() {
    this.stopped = true;
    this.release();
  }

  private async loadBlur(): Promise<boolean> {
    if (this.blur) return true;
    try {
      this.blur = await FaceBlur.create(this.info.faces);
      this.set({ blurReady: true });
      return true;
    } catch {
      this.fail(PROBLEMS.blur);
      return false;
    }
  }

  private connect() {
    const ws = new WebSocket(wsUrl(`/ws/cam/${this.token}`));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.set({ phase: "live" });
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(String(ev.data));
        if (msg.type !== "hello") return;
        const rate = msg.fps !== this.send.fps;
        this.send = { fps: msg.fps ?? this.send.fps, width: msg.width ?? this.send.width, quality: msg.quality ?? this.send.quality };
        this.mustBlur = msg.blur !== false;
        if (this.mustBlur && !this.blur) void this.loadBlur();
        if (rate) {
          window.clearInterval(this.timer);
          this.timer = window.setInterval(this.tick, 1000 / this.send.fps);
        }
      } catch {
        // not for us
      }
    };
    ws.onclose = (ev) => {
      if (this.stopped || this.ws !== ws) return;
      this.ws = null;
      const end = ENDS[ev.code];
      if (end) {
        this.set({ phase: end });
        this.release();
        return;
      }
      this.set({ phase: "reconnecting" });
      const wait = ev.code === 4429 ? BUSY_WAIT_MS : RETRY_MS[Math.min(this.retry, RETRY_MS.length - 1)];
      this.retry += 1;
      this.retryTimer = window.setTimeout(() => !this.stopped && this.connect(), wait);
    };
  }

  private tick = () => {
    const v = this.video;
    if (this.busy || !v.videoWidth || this.stopped) return;
    const c = this.canvas;
    const scale = Math.min(1, this.send.width / v.videoWidth);
    const w = Math.round(v.videoWidth * scale);
    const h = Math.round(v.videoHeight * scale);
    if (c.width !== w || c.height !== h) Object.assign(c, { width: w, height: h });
    const ctx = c.getContext("2d", { willReadFrequently: true });
    if (!ctx) return;
    ctx.drawImage(v, 0, 0, w, h);
    let safe = !this.mustBlur;
    if (this.mustBlur && this.blur) {
      try {
        this.set({ covered: this.blur.apply(c) });
        safe = true;
      } catch {
        safe = false; // never send a frame whose faces were not looked for
      }
    }
    const ws = this.ws;
    if (!safe || !ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > MAX_BUFFERED) return;
    this.busy = true;
    c.toBlob((b) => {
      this.busy = false;
      if (b && ws.readyState === WebSocket.OPEN) {
        ws.send(b);
        this.set({ sent: this.state.sent + 1 });
      }
    }, "image/jpeg", this.send.quality);
  };

  private async keepAwake() {
    try {
      const nav = navigator as unknown as { wakeLock?: { request: (t: "screen") => Promise<{ release: () => Promise<void> }> } };
      this.wake = (await nav.wakeLock?.request("screen")) ?? null;
    } catch {
      // the screen may still sleep; the page says to keep it on
    }
  }

  private fail(problem: string) {
    this.set({ phase: "error", problem });
    this.release();
  }

  private release() {
    window.clearInterval(this.timer);
    window.clearTimeout(this.retryTimer);
    const ws = this.ws;
    this.ws = null;
    ws?.close(1000);
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.video.srcObject = null;
    void this.wake?.release().catch(() => {});
    this.wake = null;
  }
}
