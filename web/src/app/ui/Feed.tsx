import { useState } from "react";
import { Link } from "react-router-dom";
import { at, epoch, plain } from "../../format";
import type { Incident } from "../../types";
import { isActive, sentence, statusWords } from "../words";
import { SeverityTag } from "./Severity";

function statusLine(inc: Incident): string | null {
  const seen = inc.count > 1 ? `seen ${inc.count} times` : "";
  if (inc.status === "open") return seen ? sentence(seen) : null;
  return [statusWords(inc), seen].filter(Boolean).join(", ");
}

// One incident in plain words: "Someone in the pool after hours", "Pool, 10:42 PM".
export function IncidentRow({ incident, camera }: { incident: Incident; camera?: string }) {
  const status = statusLine(incident);
  return (
    <Link to={`/app/incidents/${incident.id}`} className={`inc-row${isActive(incident) ? "" : " is-closed"}`}>
      <SeverityTag level={incident.severity} />
      <span>
        <span className="inc-what">{sentence(plain(incident.title || incident.reason))}</span>
        <span className="inc-where">{[camera, at(epoch(incident.opened_at))].filter(Boolean).join(", ")}</span>
        {status && <span className="inc-status">{status}</span>}
      </span>
    </Link>
  );
}

const GROUP: Record<Incident["status"], number> = { open: 0, acknowledged: 1, false_alarm: 2, resolved: 2 };
const LEVEL: Record<Incident["severity"], number> = { critical: 0, warning: 1, info: 2 };

// Open incidents first, the most serious on top, then those someone is handling, then the closed ones; newest first
// within each.
export function sortIncidents(list: Incident[]): Incident[] {
  return [...list].sort((a, b) =>
    GROUP[a.status] - GROUP[b.status]
    || (a.status === "open" ? LEVEL[a.severity] - LEVEL[b.severity] : 0)
    || epoch(b.last_at) - epoch(a.last_at));
}

export function Feed({ incidents, names, label, limit = 12 }: {
  incidents: Incident[];
  names: Record<string, string>;
  label: string;
  limit?: number;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? incidents : incidents.slice(0, limit);
  return (
    <>
      <ul className="list feed" aria-label={label}>
        {shown.map((i) => (
          <li key={i.id}><IncidentRow incident={i} camera={i.camera_id ? names[i.camera_id] : undefined} /></li>
        ))}
      </ul>
      {incidents.length > shown.length && (
        <div className="inc-more">
          <button type="button" className="btn btn-quiet btn-sm" onClick={() => setAll(true)}>
            Show {incidents.length - shown.length} more
          </button>
        </div>
      )}
    </>
  );
}
