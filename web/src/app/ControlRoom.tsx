import { useMemo } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import brand from "../site/brand";
import { epoch, plural } from "../format";
import { useLoad, useTitle } from "../hooks";
import { useCan } from "./session";
import { Feed, sortIncidents } from "./ui/Feed";
import { Empty, Page, PageHead, Problem } from "./ui/Page";
import { CameraTile } from "./ui/Tile";
import { healthOf, isActive } from "./words";

const DAY_S = 86_400;

// Home: the camera wall and one feed of incidents. Nothing else.
export default function ControlRoom() {
  useTitle("Control room");
  const admin = useCan("admin");
  const cameras = useLoad(() => api.cameras(), [], 10_000);
  const incidents = useLoad(() => api.incidents(), [], 10_000);
  const list = cameras.data ?? [];
  const names = useMemo(() => Object.fromEntries(list.map((c) => [c.id, c.name])), [list]);

  const now = Date.now() / 1000;
  // info is for the logs and the daily summary; the room shows what needs a person
  const feed = sortIncidents((incidents.data ?? []).filter((i) => i.severity !== "info" && (isActive(i) || now - epoch(i.last_at) < DAY_S)));
  const open = feed.filter((i) => i.status === "open");
  const alerting = new Set(open.map((i) => i.camera_id));
  const live = list.filter((c) => healthOf(c).word === "Live").length;
  const trouble = list.filter((c) => ["bad", "down"].includes(healthOf(c).tone)).length;

  return (
    <Page>
      <PageHead
        title="Control room"
        actions={admin && list.length > 0 && <Link className="btn btn-primary" to="/app/cameras/new">Add a camera</Link>}
      />
      <div className="control">
        <section className="control-feed section" aria-labelledby="feed-title">
          <div className="section-head">
            <h2 id="feed-title">Incidents</h2>
            <span className="small muted">{open.length ? `${open.length} open` : "Last 24 hours"}</span>
          </div>
          <Problem>{incidents.error}</Problem>
          <div className="card">
            {feed.length > 0 ? (
              <Feed incidents={feed} names={names} label="Incidents" />
            ) : (
              <p className="quiet" role="status">
                <strong>All quiet</strong>
                {list.length === 0
                  ? `Add a camera and tell ${brand.name} what to watch for. Incidents show here the moment a watch spots something.`
                  : "Nothing needs a look. Incidents show here the moment a watch spots something."}
              </p>
            )}
          </div>
        </section>

        <section className="control-wall section" aria-labelledby="wall-title">
          <div className="section-head">
            <h2 id="wall-title">Cameras</h2>
            {list.length > 0 && (
              <span className="small muted">
                {live} of {list.length} live{trouble > 0 && <>, <span className="state-bad">{plural(trouble, "needs", "need")} a look</span></>}
              </span>
            )}
          </div>
          <Problem>{cameras.error}</Problem>
          {cameras.loading && !cameras.data ? null : list.length === 0 ? (
            <Empty
              title="No cameras yet"
              action={admin ? <Link className="btn btn-primary" to="/app/cameras/new">Add a camera</Link> : undefined}
            >
              {admin
                ? "Add your first camera: this laptop or phone, or the address of a camera on your network."
                : "Ask your society's admin to add the cameras."}
            </Empty>
          ) : (
            <ul className="list wall" aria-label="Cameras">
              {list.map((c) => <CameraTile key={c.id} camera={c} alert={alerting.has(c.id)} />)}
            </ul>
          )}
        </section>
      </div>
    </Page>
  );
}
