import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { frameUrl } from "../../api";
import type { Camera } from "../../types";
import { healthOf } from "../words";
import { Health } from "./Health";

const STILL_EVERY_MS = 15_000;

// The camera's latest picture, swapped in only once the next one has loaded, so a slow camera never flickers.
export function LiveStill({ camera, label }: { camera: Camera; label?: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const { tone } = healthOf(camera);
  const refresh = camera.enabled && camera.kind !== "file" && tone !== "down";

  useEffect(() => {
    let gone = false;
    const load = () => {
      if (document.visibilityState !== "visible") return;
      const url = frameUrl(camera.id, String(Date.now()));
      const img = new Image();
      img.onload = () => !gone && (setSrc(url), setFailed(false));
      img.onerror = () => !gone && setFailed(true);
      img.src = url;
    };
    const first = window.setTimeout(load, Math.random() * 600);
    const id = refresh ? window.setInterval(load, STILL_EVERY_MS) : 0;
    return () => {
      gone = true;
      window.clearTimeout(first);
      window.clearInterval(id);
    };
  }, [camera.id, refresh]);

  return (
    <div className="thumb thumb-fill">
      {src && !(failed && !refresh) ? <img src={src} alt="" /> : <span>{failed || !refresh ? label ?? "No picture yet" : ""}</span>}
    </div>
  );
}

export function CameraTile({ camera, alert }: { camera: Camera; alert: boolean }) {
  return (
    <li className={alert ? "tile-alert" : undefined}>
      <Link to={`/app/cameras/${camera.id}`} className="tile-link">
        <div className="tile-pic">
          <LiveStill camera={camera} />
        </div>
        <div className="tile-meta">
          <span className="tile-name">{camera.name}</span>
          <Health camera={camera} alert={alert} />
        </div>
      </Link>
    </li>
  );
}
