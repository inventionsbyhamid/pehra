import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode, type RefObject } from "react";
import { api, wsUrl } from "../api";
import type { Alert, LiveMessage, LiveTrack, PipelineStats, Zone } from "../types";

export type LiveStatus = { source: string; fps: number; loops: number; stats: PipelineStats };
export type LiveHandle = { snapshot: () => string | null };

type Props = {
  cameraId: string;
  zones: Zone[];
  // "frames": the server sends plain frames; "overlay": the picture is this browser's own webcam video
  mode: "frames" | "overlay";
  video?: RefObject<HTMLVideoElement>;
  videoActive?: boolean;
  onStatus?: (s: LiveStatus) => void;
  onAlert?: (a: Alert) => void;
  idle?: string;
  children?: ReactNode;
};

type Rect = { x: number; y: number; w: number; h: number };

const STALE_MS = 1500;
const POSTURE_WORDS: Record<string, string> = { fallen: "has fallen", falling: "falling" };

function b64ToBlob(b64: string): Blob {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: "image/jpeg" });
}

function fit(sw: number, sh: number, w: number, h: number): Rect {
  const s = Math.min(w / sw, h / sh);
  return { x: (w - sw * s) / 2, y: (h - sh * s) / 2, w: sw * s, h: sh * s };
}

function cssVar(name: string, fallback: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function pill(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, bg: string, dpr: number, maxX: number) {
  const padX = 6 * dpr;
  const h = 20 * dpr;
  const w = ctx.measureText(text).width + padX * 2;
  const left = Math.max(0, Math.min(x, maxX - w));
  ctx.fillStyle = bg;
  ctx.beginPath();
  ctx.roundRect(left, y, w, h, 4 * dpr);
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.fillText(text, left + padX, y + h / 2 + 0.5 * dpr);
}

function drawOverlay(ctx: CanvasRenderingContext2D, r: Rect, zones: Zone[], tracks: LiveTrack[], dpr: number, width: number): number {
  ctx.font = `600 ${12.5 * dpr}px "Atkinson Hyperlegible Next", system-ui, sans-serif`;
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  const px = (x: number) => r.x + x * r.w;
  const py = (y: number) => r.y + y * r.h;

  zones.forEach((z, i) => {
    if (z.polygon.length < 3) return;
    const color = cssVar(`--zone-${(i % 4) + 1}`, "#2a78d6");
    ctx.beginPath();
    z.polygon.forEach(([x, y], j) => (j ? ctx.lineTo(px(x), py(y)) : ctx.moveTo(px(x), py(y))));
    ctx.closePath();
    ctx.globalAlpha = 0.12;
    ctx.fillStyle = color;
    ctx.fill();
    ctx.globalAlpha = 0.9;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2 * dpr;
    ctx.stroke();
    ctx.globalAlpha = 1;
    const top = z.polygon.reduce((a, b) => (b[1] < a[1] || (b[1] === a[1] && b[0] < a[0]) ? b : a));
    pill(ctx, z.name, px(top[0]) + 6 * dpr, py(top[1]) + 6 * dpr, "rgb(16 24 22 / 0.7)", dpr, width);
  });

  for (const t of tracks) {
    const [bx, by, bw, bh] = t.box;
    const x = px(bx), y = py(by), w = bw * r.w, h = bh * r.h;
    const alarm = !!t.posture && t.posture !== "upright";
    ctx.lineWidth = 4.5 * dpr;
    ctx.strokeStyle = "rgb(0 0 0 / 0.35)";
    ctx.strokeRect(x, y, w, h);
    ctx.lineWidth = (alarm ? 3 : 2) * dpr;
    ctx.strokeStyle = alarm ? "#ff8f80" : "rgb(255 255 255 / 0.92)";
    ctx.strokeRect(x, y, w, h);
    const name = t.label || t.cls;
    const text = alarm ? `${name}, ${POSTURE_WORDS[t.posture!] ?? t.posture}` : name;
    const ly = y - 24 * dpr >= r.y ? y - 24 * dpr : y + 4 * dpr;
    pill(ctx, text, x, ly, alarm ? "#a3302a" : "rgb(16 24 22 / 0.72)", dpr, width);
  }
  return tracks.length;
}

export const LiveView = forwardRef<LiveHandle, Props>(function LiveView(
  { cameraId, zones, mode, video, videoActive, onStatus, onAlert, idle, children }, ref) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<"connecting" | "open" | "live" | "closed">("connecting");
  const [aspect, setAspect] = useState(4 / 3);
  const frame = useRef<ImageBitmap | null>(null);
  const tracks = useRef<{ list: LiveTrack[]; at: number }>({ list: [], at: 0 });
  const received = useRef(0);
  const zonesRef = useRef(zones);
  zonesRef.current = zones;
  const handlers = useRef({ onStatus, onAlert });
  handlers.current = { onStatus, onAlert };

  const draw = useRef(() => {});
  draw.current = () => {
    const c = canvas.current;
    const ctx = c?.getContext("2d");
    if (!c || !ctx) return;
    ctx.clearRect(0, 0, c.width, c.height);
    const dpr = window.devicePixelRatio || 1;
    let r: Rect | null = null;
    if (mode === "frames" && frame.current) {
      r = fit(frame.current.width, frame.current.height, c.width, c.height);
      ctx.drawImage(frame.current, r.x, r.y, r.w, r.h);
    } else if (mode === "overlay" && video?.current?.videoWidth) {
      r = fit(video.current.videoWidth, video.current.videoHeight, c.width, c.height);
    }
    if (!r) return;
    const fresh = mode === "frames" || Date.now() - tracks.current.at < STALE_MS;
    const n = drawOverlay(ctx, r, zonesRef.current, fresh ? tracks.current.list : [], dpr, c.width);
    c.dataset.boxes = String(n);
    c.dataset.received = String(received.current);
  };

  useImperativeHandle(ref, () => ({
    snapshot: () => {
      const src = mode === "frames" ? frame.current : video?.current?.videoWidth ? video.current : null;
      if (!src) return null;
      const w = src instanceof HTMLVideoElement ? src.videoWidth : src.width;
      const h = src instanceof HTMLVideoElement ? src.videoHeight : src.height;
      const off = document.createElement("canvas");
      off.width = w;
      off.height = h;
      off.getContext("2d")?.drawImage(src, 0, 0, w, h);
      return off.toDataURL("image/jpeg", 0.9);
    },
  }), [mode, video]);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const ro = new ResizeObserver(() => {
      const dpr = window.devicePixelRatio || 1;
      c.width = Math.round(c.clientWidth * dpr);
      c.height = Math.round(c.clientHeight * dpr);
      draw.current();
    });
    ro.observe(c);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    draw.current();
  }, [zones]);

  useEffect(() => {
    if (mode !== "overlay" || !video?.current) return;
    const v = video.current;
    const sized = () => {
      if (v.videoWidth) setAspect(v.videoWidth / v.videoHeight);
      draw.current();
    };
    v.addEventListener("loadedmetadata", sized);
    v.addEventListener("resize", sized);
    // boxes fade once they are older than the last analysis
    const id = window.setInterval(() => draw.current(), 500);
    return () => {
      v.removeEventListener("loadedmetadata", sized);
      v.removeEventListener("resize", sized);
      window.clearInterval(id);
    };
  }, [mode, video]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry = 1000;
    let timer = 0;
    let decoding = false;

    const connect = () => {
      // overlay: this browser shows its own camera, so the server sends boxes without pictures
      ws = new WebSocket(wsUrl(`/ws/cameras/${cameraId}${mode === "overlay" ? "?frames=0" : ""}`));
      ws.onopen = () => {
        retry = 1000;
        setState((s) => (s === "live" ? s : "open"));
      };
      ws.onmessage = async (ev) => {
        const msg = JSON.parse(ev.data) as LiveMessage;
        if (msg.type === "frame") {
          if (decoding) return;
          decoding = true;
          try {
            const bmp = await createImageBitmap(b64ToBlob(msg.jpeg_b64));
            frame.current?.close();
            frame.current = bmp;
            tracks.current = { list: msg.tracks ?? [], at: Date.now() };
            received.current += 1;
            setAspect((a) => (Math.abs(a - bmp.width / bmp.height) > 0.01 ? bmp.width / bmp.height : a));
            draw.current();
            setState("live");
          } catch {
            // a bad frame is skipped
          } finally {
            decoding = false;
          }
        } else if (msg.type === "overlay") {
          tracks.current = { list: msg.tracks ?? [], at: Date.now() };
          received.current += 1;
          draw.current();
          setState("live");
        } else if (msg.type === "status") {
          handlers.current.onStatus?.({ source: msg.source_status, fps: msg.fps, loops: msg.loops, stats: msg.stats });
        } else if (msg.type === "alert") {
          handlers.current.onAlert?.(msg.alert);
        }
      };
      ws.onclose = (ev) => {
        if (stopped) return;
        if (ev.code === 4401) {
          api.me().catch(() => {});
          return;
        }
        setState((s) => (s === "live" ? s : "closed"));
        timer = window.setTimeout(connect, retry);
        retry = Math.min(retry * 2, 15000);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      ws?.close();
      frame.current?.close();
      frame.current = null;
    };
  }, [cameraId, mode]);

  const showMsg = mode === "frames" ? state !== "live" : !videoActive;
  return (
    <div className="stage live" style={{ aspectRatio: String(aspect) }}>
      {children}
      <canvas ref={canvas} className="live-canvas" role="img" data-mode={mode}
        aria-label="Live camera picture with tracked objects and zones drawn on it" />
      {showMsg && (
        <div className="stage-msg" role="status">
          <p>{state === "closed" ? "Lost the connection to the camera. Reconnecting." : idle ?? "Waiting for the first picture."}</p>
        </div>
      )}
    </div>
  );
});
