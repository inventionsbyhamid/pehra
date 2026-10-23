import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { api, wsUrl } from "../api";
import type { Alert, LiveMessage, PipelineStats } from "../types";

export type LiveStatus = { source: string; fps: number; loops: number; stats: PipelineStats };
export type LiveHandle = { snapshot: () => string | null };

type Props = {
  cameraId: string;
  onStatus?: (s: LiveStatus) => void;
  onAlert?: (a: Alert) => void;
  idle?: string;
};

function b64ToBlob(b64: string): Blob {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: "image/jpeg" });
}

export const LiveView = forwardRef<LiveHandle, Props>(function LiveView({ cameraId, onStatus, onAlert, idle }, ref) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState<"connecting" | "open" | "frames" | "closed" | "signed_out">("connecting");
  const handlers = useRef({ onStatus, onAlert });
  handlers.current = { onStatus, onAlert };

  useImperativeHandle(ref, () => ({
    snapshot: () => (state === "frames" && canvas.current ? canvas.current.toDataURL("image/jpeg", 0.9) : null),
  }), [state]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry = 1000;
    let timer = 0;
    let drawing = false;

    const connect = () => {
      setState((s) => (s === "frames" ? s : "connecting"));
      ws = new WebSocket(wsUrl(`/ws/cameras/${cameraId}`));
      ws.onopen = () => {
        retry = 1000;
        setState((s) => (s === "frames" ? s : "open"));
      };
      ws.onmessage = async (ev) => {
        const msg = JSON.parse(ev.data) as LiveMessage;
        if (msg.type === "frame") {
          if (drawing) return;
          drawing = true;
          try {
            const bmp = await createImageBitmap(b64ToBlob(msg.jpeg_b64));
            const c = canvas.current;
            if (c) {
              if (c.width !== bmp.width || c.height !== bmp.height) {
                c.width = bmp.width;
                c.height = bmp.height;
              }
              c.getContext("2d")?.drawImage(bmp, 0, 0);
              setState("frames");
            }
            bmp.close();
          } catch {
            // a bad frame is skipped
          } finally {
            drawing = false;
          }
        } else if (msg.type === "status") {
          handlers.current.onStatus?.({ source: msg.source_status, fps: msg.fps, loops: msg.loops, stats: msg.stats });
        } else if (msg.type === "alert") {
          handlers.current.onAlert?.(msg.alert);
        }
      };
      let opened = false;
      ws.addEventListener("open", () => { opened = true; });
      ws.onclose = (ev) => {
        if (stopped) return;
        if (ev.code === 4401) {
          setState("signed_out");
          return;
        }
        // a refused handshake looks like a dropped line; checking the session signs out if it ended
        if (!opened) api.me().catch(() => {});
        setState((s) => (s === "frames" ? s : "closed"));
        timer = window.setTimeout(connect, retry);
        retry = Math.min(retry * 2, 15000);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, [cameraId]);

  return (
    <>
      <canvas ref={canvas} aria-label="Live camera picture with tracked objects and zones drawn on it" role="img"
        style={{ visibility: state === "frames" ? "visible" : "hidden" }} />
      {state !== "frames" && (
        <div className="stage-msg" role="status">
          <p>
            {state === "signed_out" ? "Your session ended. Sign in again to see this camera."
              : state === "closed" ? "Lost the connection to the camera. Reconnecting."
              : idle ?? "Waiting for the first picture."}
          </p>
        </div>
      )}
    </>
  );
});
