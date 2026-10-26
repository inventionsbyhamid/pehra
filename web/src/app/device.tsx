import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode, type RefObject } from "react";
import { Link, useLocation } from "react-router-dom";
import { useWebcam } from "../components/useWebcam";

// This browser as one of the society's cameras. It lives above the pages, so it keeps sending while the person adds a
// watch or looks at the control room; a bar says so on every other page.
interface Device {
  camera: { id: string; name: string } | null;
  start: (id: string, name: string) => void;
  stop: () => void;
  webcam: ReturnType<typeof useWebcam>;
}

const DeviceContext = createContext<Device | null>(null);

export function DeviceProvider({ children }: { children: ReactNode }) {
  const [camera, setCamera] = useState<{ id: string; name: string } | null>(null);
  const webcam = useWebcam(camera?.id ?? null);
  const { setOn } = webcam;
  const start = useCallback((id: string, name: string) => {
    setCamera({ id, name });
    setOn(true);
  }, [setOn]);
  const stop = useCallback(() => setOn(false), [setOn]);
  const value = useMemo(() => ({ camera, start, stop, webcam }), [camera, start, stop, webcam]);
  return <DeviceContext.Provider value={value}>{children}</DeviceContext.Provider>;
}

export function useDevice(): Device {
  const device = useContext(DeviceContext);
  if (!device) throw new Error("useDevice outside the signed in app");
  return device;
}

// The device's own picture on a page, from the stream the provider keeps.
export function DeviceVideo({ stream, video }: { stream: MediaStream | null; video: RefObject<HTMLVideoElement> }) {
  useEffect(() => {
    const v = video.current;
    if (v && v.srcObject !== stream) {
      v.srcObject = stream;
      v.play().catch(() => {});
    }
  }, [stream, video]);
  return <video ref={video} className="stage-video" muted playsInline autoPlay aria-hidden="true" />;
}

export function DeviceBar() {
  const { camera, stop, webcam } = useDevice();
  const { pathname } = useLocation();
  if (!camera || !webcam.on || pathname === `/app/cameras/${camera.id}`) return null;
  return (
    <div className="device-bar" role="region" aria-label="This device">
      <div className="wrap device-bar-row">
        <p>
          <span className="live-dot" aria-hidden="true" />
          This device is sending pictures for {camera.name}. Keep this tab open.
        </p>
        <div className="row">
          <Link className="btn btn-sm" to={`/app/cameras/${camera.id}`}>Open {camera.name}</Link>
          <button type="button" className="btn btn-sm btn-quiet" onClick={stop}>Stop</button>
        </div>
      </div>
    </div>
  );
}
