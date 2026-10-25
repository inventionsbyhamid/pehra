import { Link } from "react-router-dom";
import { ago, plain, when } from "../format";
import type { Alert } from "../types";
import { AlertStatusTag, SeverityTag } from "./Tags";
import { Thumb } from "./Thumb";

export function AlertSummary({ alert, camera, active }: { alert: Alert; camera?: string; active?: boolean }) {
  return (
    <Link to={`/alerts/${alert.id}`} className={`alert-row sev-${alert.severity}${active ? " is-active" : ""}`}
      aria-current={active ? "true" : undefined}>
      <Thumb src={alert.keyframe_url ?? null} alt="" w={84} h={63} />
      <div className="grow stack" style={{ gap: 4 }}>
        <p className="alert-row-summary">{plain(alert.summary)}</p>
        <div className="row" style={{ gap: 6 }}>
          {alert.severity !== "info" && <SeverityTag severity={alert.severity} />}
          <AlertStatusTag status={alert.status} />
        </div>
        <p className="xsmall muted">
          <time dateTime={new Date(alert.ts * 1000).toISOString()} title={when(alert.ts)}>{ago(alert.ts)}</time>
          {camera && <span>, {camera}</span>}
        </p>
      </div>
    </Link>
  );
}
