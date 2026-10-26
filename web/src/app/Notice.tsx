import { useState } from "react";
import { api } from "../api";
import { Mark } from "../components/Mark";
import { useLoad, useTitle } from "../hooks";
import brand from "../site/brand";
import { currentPlan } from "./Settings";
import { useSession } from "./session";
import { Page, PageHead, Problem } from "./ui/Page";
import { Link } from "react-router-dom";
import { purposeLabel } from "./words";

// One printable notice per camera zone, one to a page: what the camera is for, how long footage is kept, whom to ask.
export default function Notice() {
  useTitle("Camera zone notices");
  const { me, purposes } = useSession();
  const cameras = useLoad(() => api.cameras(), []);
  const contact = useLoad(() => api.noticeContact(), []);
  const [only, setOnly] = useState("");
  const society = me.workspace?.name ?? "";
  const days = currentPlan(me.workspace?.plan).limits.retention_days;
  const list = (cameras.data ?? []).filter((c) => c.kind !== "file" && (!only || c.id === only));

  return (
    <Page>
      <div className="no-print stack" style={{ gap: 20 }}>
        <PageHead title="Camera zone notices" back={{ to: "/app/settings/privacy", label: "Privacy" }}
          sub={<span>One page for each camera, with {society}'s name on it.</span>}
          actions={<button className="btn btn-primary" onClick={() => window.print()} disabled={!list.length}>Print</button>} />
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div className="field" style={{ flex: "1 1 18rem" }}>
            <span className="label">Whom to ask</span>
            <p>{contact.data?.shown ?? "The society office"} <Link className="small" to="/app/settings/privacy">Change</Link></p>
          </div>
          <label className="field" style={{ flex: "1 1 12rem" }}>
            <span>Cameras</span>
            <select className="select" value={only} onChange={(e) => setOnly(e.target.value)}>
              <option value="">Every camera</option>
              {(cameras.data ?? []).filter((c) => c.kind !== "file").map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
        </div>
        <Problem>{cameras.error}</Problem>
        {cameras.data && !list.length && <p className="notice">Add a camera first. Each camera gets its own notice.</p>}
      </div>
      <div className="notices">
        {list.map((c) => (
          <article key={c.id} className="notice-sheet" aria-label={`Notice for ${c.name}`}>
            <h2>CCTV in use</h2>
            <p className="notice-where">{c.name}, {society}</p>
            <dl>
              <div>
                <dt>Why this camera is here</dt>
                <dd>{purposeLabel(purposes, c.purpose)}: for the safety of residents, staff and visitors.</dd>
              </div>
              <div>
                <dt>How long footage is kept</dt>
                <dd>{days ? `${days} days, then it is deleted.` : "As long as the society has agreed, then it is deleted."}</dd>
              </div>
              <div>
                <dt>Faces</dt>
                <dd>Only adult staff who agreed are recognised by face. Nobody else is named.</dd>
              </div>
              <div>
                <dt>Questions or complaints</dt>
                <dd>{contact.data?.shown || "The society office."}</dd>
              </div>
            </dl>
            <p className="notice-foot"><Mark size={24} />Watched with {brand.name} for {society}. The society decides how footage is used.</p>
          </article>
        ))}
      </div>
    </Page>
  );
}
