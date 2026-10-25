import type { Camera } from "../../types";
import { healthOf } from "../words";

export function Health({ camera, alert }: { camera: Camera; alert?: boolean }) {
  const { word, tone } = healthOf(camera);
  if (alert && tone !== "bad" && tone !== "down") {
    return <span className="state state-bad"><span className="live-dot bad" aria-hidden="true" />Alert</span>;
  }
  return (
    <span className={`state${tone === "bad" || tone === "down" ? " state-bad" : ""}`}>
      <span className={`live-dot ${tone}`} aria-hidden="true" />{word}
    </span>
  );
}
