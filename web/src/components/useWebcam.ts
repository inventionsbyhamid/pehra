import { useEffect, useRef, useState } from "react";
import { wsUrl } from "../api";
import brand from "../site/brand";

const FPS = 5;
const WIDTH = 960;

export type WebcamState = "off" | "starting" | "on" | "error";

// Sends this browser's camera to the server as JPEG frames over a WebSocket.
export function useWebcam(cameraId: string) {
  const [on, setOn] = useState(false);
  const [state, setState] = useState<WebcamState>("off");
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(0);
  const video = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    if (!on) {
      setState("off");
      return;
    }
    let stream: MediaStream | null = null;
    let ws: WebSocket | null = null;
    let timer = 0;
    let cancelled = false;
    let frames = 0;
    const canvas = document.createElement("canvas");

    (async () => {
      setState("starting");
      setError(null);
      if (!navigator.mediaDevices?.getUserMedia) {
        setState("error");
        setError(`This browser can't share its camera here. Open ${brand.name} over https, or in a current browser.`);
        setOn(false);
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: WIDTH } }, audio: false });
      } catch (e) {
        if (cancelled) return;
        const name = (e as DOMException).name;
        setState("error");
        setError(name === "NotAllowedError"
          ? "Camera access was blocked. Allow the camera for this site in your browser settings, then try again."
          : name === "NotFoundError" ? "No camera was found on this device." : "The camera could not be started. Close other apps using it and try again.");
        setOn(false);
        return;
      }
      if (cancelled) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      const v = video.current ?? document.createElement("video");
      v.muted = true;
      v.playsInline = true;
      v.srcObject = stream;
      await v.play().catch(() => {});
      ws = new WebSocket(wsUrl(`/ws/cameras/${cameraId}/push`));
      ws.binaryType = "arraybuffer";
      ws.onopen = () => setState("on");
      ws.onclose = (ev) => {
        if (cancelled) return;
        setState("error");
        setError(ev.code === 4401 ? "Your session ended. Sign in again to use this device as a camera."
          : ev.code === 4403 ? "Only a society admin can use this browser as a camera."
          : ev.code === 4409 ? "Another device is now sending this camera, so this one stopped."
          : ev.code === 4429 && ev.reason ? ev.reason
          : `The connection to ${brand.name} dropped. Start the camera again.`);
        setOn(false);
      };
      timer = window.setInterval(() => {
        if (!ws || ws.readyState !== WebSocket.OPEN || ws.bufferedAmount > 1_000_000 || !v.videoWidth) return;
        const scale = Math.min(1, WIDTH / v.videoWidth);
        canvas.width = Math.round(v.videoWidth * scale);
        canvas.height = Math.round(v.videoHeight * scale);
        canvas.getContext("2d")?.drawImage(v, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((b) => {
          if (b && ws?.readyState === WebSocket.OPEN) {
            ws.send(b);
            frames += 1;
            if (frames % FPS === 0) setSent(frames);
          }
        }, "image/jpeg", 0.75);
      }, 1000 / FPS);
    })();

    return () => {
      cancelled = true;
      window.clearInterval(timer);
      ws?.close();
      stream?.getTracks().forEach((t) => t.stop());
      if (video.current) video.current.srcObject = null;
    };
  }, [on, cameraId]);

  return { on, setOn, state, error, sent, video };
}
