import { Link, Navigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useLoad, useTitle } from "../hooks";
import brand, { mailto } from "../site/brand";
import { cameraPlan, camerasText, plans, retentionText, rupees } from "../site/plans";
import { Numbers } from "./Numbers";
import { atLeast, useSession } from "./session";
import { Team } from "./Team";
import { Telegram } from "./Telegram";
import { Fact, Facts } from "./ui/Card";
import { Page } from "./ui/Page";

const ROLE_WORDS = { viewer: "Viewer", supervisor: "Supervisor", admin: "Admin", owner: "Owner" } as const;

const TABS = [
  { id: "society", label: "Society" },
  { id: "team", label: "Team" },
  { id: "telegram", label: "Telegram" },
  { id: "privacy", label: "Privacy" },
  { id: "billing", label: "Billing" },
  { id: "numbers", label: "Numbers", admin: true },
];

export function currentPlan(id: string | undefined) {
  return plans.find((p) => p.id === id) ?? plans.find((p) => p.price_month_inr === 0) ?? plans[0]!;
}

function Society() {
  const { me, reload, signOut } = useSession();
  const others = (me.workspaces ?? []).filter((w) => w.id !== me.workspace?.id);
  const plan = currentPlan(me.workspace?.plan);
  async function switchTo(id: string) {
    await api.switchSociety(id).catch(() => {});
    reload();
  }
  return (
    <>
      <h1>Society</h1>
      <section className="card">
        <Facts label="Your society">
          <Fact term="Name">{me.workspace?.name}</Fact>
          <Fact term="Time zone">{me.workspace?.timezone}</Fact>
          <Fact term="Plan"><Link to="/app/settings/billing">{plan.name}</Link></Fact>
          <Fact term="You">
            {me.user.name || me.user.email}, {me.role ? ROLE_WORDS[me.role].toLowerCase() : "member"}
          </Fact>
        </Facts>
      </section>
      {others.length > 0 && (
        <section className="section" aria-labelledby="other-title">
          <h2 id="other-title" className="h2">Your other societies</h2>
          <ul className="list card people-rows">
            {others.map((w) => (
              <li key={w.id}>
                <span className="grow"><strong>{w.name}</strong> <span className="muted small">{ROLE_WORDS[w.role]}</span></span>
                <button className="btn btn-sm" onClick={() => switchTo(w.id)}>Switch to {w.name}</button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <div><button className="btn" onClick={signOut}>Sign out</button></div>
    </>
  );
}

function Privacy() {
  const { me } = useSession();
  const plan = currentPlan(me.workspace?.plan);
  const days = plan.limits.retention_days;
  return (
    <>
      <h1>Privacy</h1>
      <section className="card stack">
        <h2 className="h2">How long footage is kept</h2>
        <p>
          {days ? `Footage and pictures are kept for ${days} days on ${plan.name}, then deleted on their own.` : `${retentionText(plan)}.`}
          {" "}Incident clips follow the same schedule.
        </p>
        <p className="small muted">
          Faces are recognised only for adult staff who agreed. Telegram gets a still picture with other faces blurred; clips stay
          behind sign in, and every time someone plays one it is noted.
        </p>
      </section>
      <section className="card stack">
        <h2 className="h2">Notice for each camera zone</h2>
        <p>Put a printed notice where each camera can see people. It says what the camera is for, how long footage is kept and whom to ask.</p>
        <div><Link className="btn btn-primary" to="/app/settings/privacy/notice">Print the notices</Link></div>
      </section>
      <p className="small">Read the full <Link to="/privacy">privacy notice</Link> and the <Link to="/terms">terms</Link>.</p>
    </>
  );
}

function Billing() {
  const { me } = useSession();
  const cameras = useLoad(() => api.cameras(), []);
  const plan = currentPlan(me.workspace?.plan);
  const watched = (cameras.data ?? []).filter((c) => c.kind !== "file").length;
  const most = plan.limits.watched_cameras;
  return (
    <>
      <h1>Billing</h1>
      <section className="card">
        <p className="small muted">Your plan</p>
        <h2 className="h2" style={{ marginTop: 4 }}>{plan.name}</h2>
        {plan.price_month_inr ? (
          <p className="price-now" style={{ marginTop: 8 }}>
            {rupees(plan.price_month_inr)} <span>{plan.per_camera ? "a watched camera a month" : "a month"}</span>
          </p>
        ) : null}
        <Facts>
          <Fact term="Cameras">
            {camerasText(plan)}{cameras.data ? `, ${watched} in use` : ""}
            {most !== null && watched > most && <span className="hint" style={{ display: "block" }}>That is more than this plan includes.</span>}
          </Fact>
          <Fact term="Footage">{plan.limits.retention_days ? `Kept ${plan.limits.retention_days} days` : retentionText(plan)}</Fact>
        </Facts>
      </section>
      {plan.id !== cameraPlan.id && !plan.contact && (
        <section className="card stack">
          <h2 className="h2">Watch more cameras</h2>
          <p>{cameraPlan.name} is {rupees(cameraPlan.price_month_inr!)} a watched camera a month, or {rupees(cameraPlan.price_year_inr!)} a year, with {cameraPlan.limits.retention_days} days of footage.</p>
          <p className="small">
            Paying here in the app comes soon. Until then, write to <a href={mailto(`${brand.name}: ${cameraPlan.name} plan for ${me.workspace?.name}`)}>{brand.contact_email}</a> and we set it up the same day.
            {" "}<Link to="/pricing">See every plan</Link>.
          </p>
        </section>
      )}
    </>
  );
}

export default function Settings() {
  const { tab = "society" } = useParams();
  const { me } = useSession();
  const admin = atLeast(me.role, "admin");
  const tabs = TABS.filter((t) => !t.admin || admin);
  const current = tabs.find((t) => t.id === tab);
  useTitle(current ? `${current.label} settings` : "Settings");
  if (!current) return <Navigate to="/app/settings/society" replace />;

  return (
    <Page>
      <div className="settings">
        <nav className="subnav" aria-label="Settings">
          {tabs.map((t) => (
            <Link key={t.id} to={`/app/settings/${t.id}`} aria-current={t.id === tab ? "page" : undefined}>{t.label}</Link>
          ))}
        </nav>
        <div className="settings-body">
          {tab === "society" && <Society />}
          {tab === "team" && <Team />}
          {tab === "telegram" && <Telegram />}
          {tab === "privacy" && <Privacy />}
          {tab === "billing" && <Billing />}
          {tab === "numbers" && <Numbers />}
        </div>
      </div>
    </Page>
  );
}
