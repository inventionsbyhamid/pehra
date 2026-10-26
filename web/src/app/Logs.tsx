import { useMemo, useState, type ReactNode } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { api, csvUrl } from "../api";
import { clock, dayTitle, epoch, plural, shiftDay, stayed, todayKey } from "../format";
import { useLoad, useTitle } from "../hooks";
import brand from "../site/brand";
import type { AttendanceRow, Camera, Passage } from "../types";
import { Fact, Facts } from "./ui/Card";
import { Dialog, useDialog } from "./ui/Dialog";
import { Empty, Page, PageHead, Problem } from "./ui/Page";
import { PLATE_WORDS, sentence } from "./words";

const LOGS = [
  { id: "vehicles", label: "Gate log" },
  { id: "staff", label: "Staff register" },
] as const;

const REFRESH_MS = 30_000;

function DayPicker({ day, onChange }: { day: string; onChange: (d: string) => void }) {
  const today = todayKey();
  return (
    <div className="daypick">
      <button type="button" className="btn btn-sm" aria-label="Day before" onClick={() => onChange(shiftDay(day, -1))}>
        <span aria-hidden="true">&#8249;</span>
      </button>
      <input type="date" className="input" value={day} max={today} aria-label="Day"
        onChange={(e) => e.target.value && onChange(e.target.value > today ? today : e.target.value)} />
      <button type="button" className="btn btn-sm" aria-label="Day after" disabled={day >= today}
        onClick={() => onChange(shiftDay(day, 1))}>
        <span aria-hidden="true">&#8250;</span>
      </button>
    </div>
  );
}

function Toolbar({ day, setDay, search, setSearch, searchLabel, placeholder }: {
  day: string; setDay: (d: string) => void; search: string; setSearch: (s: string) => void;
  searchLabel: string; placeholder: string;
}) {
  return (
    <div className="toolbar">
      <DayPicker day={day} onChange={setDay} />
      <input className="input toolbar-search" type="search" value={search} aria-label={searchLabel} placeholder={placeholder}
        onChange={(e) => setSearch(e.target.value)} />
    </div>
  );
}

function Summary({ day, children, csv }: { day: string; children?: ReactNode; csv: string }) {
  return (
    <div className="log-summary">
      <p aria-live="polite"><strong>{dayTitle(day)}</strong>{children && <span>{children}</span>}</p>
      <a className="btn btn-quiet btn-sm" href={csv} download>Download CSV</a>
    </div>
  );
}

// The staff register's field names in one place. It says when someone was seen; in and out only come from a
// crossing of a directed line.
function seen(r: AttendanceRow) {
  return {
    first: r.first_seen ?? r.first_in ?? null, last: r.last_seen ?? r.last_out ?? null,
    cameIn: r.came_in ?? null, wentOut: r.went_out ?? null,
  };
}

// how long a vehicle was in, in words, from the row's own side of the pair
function inside(p: Passage, today: boolean, now: number): string {
  if (p.stayed_s != null) return stayed(p.stayed_s);
  if (!p.plate) return "Unknown, plate not read";
  if (p.direction === "out") return "No entry seen";
  return today ? `Still inside, ${stayed(now - epoch(p.at))}` : "No exit seen";
}

// every watch about stays (the built in overstay and any a society typed) fires on a vehicle staying too long
function Flags({ p }: { p: Passage }) {
  return p.alerted.length ? <span className="sev sev-warning">Overstay</span> : null;
}

// the time inside as the end of a phone card's line: "Main gate, inside 2 h 5 min"
function insideShort(p: Passage, today: boolean, now: number): string {
  if (p.stayed_s != null) return `inside ${stayed(p.stayed_s)}`;
  if (!p.plate) return "time inside unknown";
  const words = inside(p, today, now);
  return words[0]!.toLowerCase() + words.slice(1);
}

function dayWords(day: string) {
  const d = dayTitle(day);
  return d === "Today" || d === "Yesterday" ? d.toLowerCase() : d;
}

function plateWords(p: Passage) {
  return PLATE_WORDS[p.plate_colour] ?? null;
}

function PassageDialog({ dialog, p, today, now }: {
  dialog: ReturnType<typeof useDialog>; p: Passage | null; today: boolean; now: number;
}) {
  return (
    <Dialog dialog={dialog} id="passage-title" title={p ? p.plate ?? "Plate not read" : "Vehicle"}>
      {p && (
        <>
          {p.picture_url && <img className="passage-pic" src={p.picture_url} alt={`The vehicle's plate, ${p.plate ?? "not read"}`} />}
          <Facts label="This passage">
            <Fact term="When">{clock(epoch(p.at))}, {dayWords(p.at.slice(0, 10))}</Fact>
            <Fact term="Way">{p.direction === "in" ? "Came in" : "Went out"} through {p.camera}{p.line ? `, ${p.line}` : ""}</Fact>
            <Fact term="Vehicle">{sentence(p.vehicle)}{plateWords(p) ? `, ${plateWords(p)!.toLowerCase()}` : ""}</Fact>
            <Fact term="Plate">
              {p.plate ? <>{p.plate}{p.plate_conf != null && <span className="hint" style={{ display: "block" }}>Read with {Math.round(p.plate_conf * 100)}% confidence</span>}</>
                : "Not read. It can't be paired with its other passage."}
            </Fact>
            <Fact term="Time inside">
              {inside(p, today, now)}
              {p.paired_at && <span className="hint" style={{ display: "block" }}>{p.direction === "in" ? "Went out" : "Came in"} at {clock(epoch(p.paired_at))}</span>}
            </Fact>
            {p.alerted.length > 0 && (
              <Fact term="Overstay">
                Stayed longer than a watch about stays allows, so that watch fired.
              </Fact>
            )}
          </Facts>
          <div><button type="button" className="btn" onClick={dialog.close}>Close</button></div>
        </>
      )}
    </Dialog>
  );
}

function GateLog({ cameras }: { cameras: Camera[] | null }) {
  useTitle("Gate log");
  const [day, setDay] = useState(todayKey());
  const [search, setSearch] = useState("");
  const q = search.trim();
  const today = day === todayKey();
  const log = useLoad(() => api.gateLog(day, q), [day, q], today ? REFRESH_MS : undefined);
  const [open, setOpen] = useState<Passage | null>(null);
  const dialog = useDialog();
  const now = Date.now() / 1000;
  const rows = log.data?.day === day ? log.data.passages : [];
  const reading = (cameras ?? []).filter((c) => c.readers?.includes("plates"));
  const show = (p: Passage) => {
    setOpen(p);
    dialog.open();
  };

  const c = log.data?.counts;
  return (
    <>
      <Toolbar day={day} setDay={setDay} search={search} setSearch={setSearch} searchLabel="Search a plate"
        placeholder="Search a plate, like KA01" />
      <Problem>{log.error}</Problem>
      <Summary day={day} csv={csvUrl("vehicles", { day, q })}>
        {c && log.data?.day === day ? `${c.in} in, ${c.out} out${c.unread ? `, ${plural(c.unread, "plate")} not read` : ""}` : null}
      </Summary>
      {log.loading && !log.data ? null : rows.length === 0 ? (
        q ? <Empty title={`No plate like ${q.toUpperCase()} on this day`}>Check the plate, or pick another day.</Empty>
          : cameras && reading.length === 0 ? (
            <Empty title="Plate reading is not on yet"
              action={cameras[0] ? <Link className="btn btn-primary" to={`/app/cameras/${(cameras.find((x) => /gate/i.test(`${x.purpose} ${x.name}`)) ?? cameras[0]).id}`}>Open a gate camera</Link> : undefined}>
              Open the camera that sees the gate, choose Edit and turn on plate reading. Then draw a line across the gate, so {brand.name} knows in from out.
            </Empty>
          ) : (
            <Empty title="No vehicles on this day">
              {today ? "Vehicles show here as they cross the gate line on a camera with plate reading." : "Pick another day."}
            </Empty>
          )
      ) : (
        <>
          <div className="log-wide card card-flush">
            <table className="table log-table">
              <caption className="sr-only">Vehicles in and out, {dayTitle(day)}</caption>
              <thead>
                <tr><th scope="col">Time</th><th scope="col">Way</th><th scope="col">Plate</th><th scope="col">Vehicle</th><th scope="col">Time inside</th><th scope="col">Gate</th></tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id}>
                    <td className="num">{clock(epoch(p.at))}</td>
                    <td>{p.direction === "in" ? "In" : "Out"}</td>
                    <th scope="row">
                      <button type="button" className={`plate-btn${p.plate ? "" : " unread"}`} onClick={() => show(p)}>
                        {p.plate ?? "Not read"}
                      </button>
                    </th>
                    <td>{sentence(p.vehicle)}{plateWords(p) && <span className="cell-sub">{plateWords(p)}</span>}</td>
                    <td><span className="cell-flags">{inside(p, today, now)}<Flags p={p} /></span></td>
                    <td>{p.camera}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="log-narrow list log-cards" aria-label={`Vehicles in and out, ${dayTitle(day)}`}>
            {rows.map((p) => (
              <li key={p.id}>
                <button type="button" className="log-card" onClick={() => show(p)}>
                  <span className="log-card-top"><span className="num">{clock(epoch(p.at))}</span><span>{p.direction === "in" ? "In" : "Out"}</span><Flags p={p} /></span>
                  <strong className={p.plate ? "plate" : "plate unread"}>{p.plate ?? "Plate not read"}</strong>
                  <span className="muted">{[sentence(p.vehicle), plateWords(p)?.toLowerCase()].filter(Boolean).join(", ")}</span>
                  <span className="muted">{p.camera}, {insideShort(p, today, now)}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      <PassageDialog dialog={dialog} p={open} today={today} now={now} />
    </>
  );
}

function StaffLog({ cameras }: { cameras: Camera[] | null }) {
  useTitle("Staff register");
  const [day, setDay] = useState(todayKey());
  const [search, setSearch] = useState("");
  const today = day === todayKey();
  const log = useLoad(() => api.staffRegister(day), [day], today ? REFRESH_MS : undefined);
  const all = log.data?.day === day ? log.data.staff : [];
  const words = search.trim().toLowerCase();
  const rows = useMemo(() => all.filter((r) => !words || r.name.toLowerCase().includes(words) || r.role.includes(words)), [all, words]);
  const naming = (cameras ?? []).filter((c) => c.readers?.includes("faces"));
  const when = (iso: string | null) => (iso ? clock(epoch(iso)) : null);
  const status = (r: AttendanceRow) => (r.present ? null : today ? "Not seen yet" : "Not seen");
  const line = (r: AttendanceRow) => {
    const { first, last } = seen(r);
    if (!r.present || !first) return status(r);
    return `First seen ${when(first)}${last ? `, last seen ${when(last)}` : ""}`;
  };
  const crossed = (r: AttendanceRow) => {
    const { cameIn, wentOut } = seen(r);
    return [cameIn && `came in ${when(cameIn)}`, wentOut && `went out ${when(wentOut)}`].filter(Boolean).join(", ");
  };

  return (
    <>
      <Toolbar day={day} setDay={setDay} search={search} setSearch={setSearch} searchLabel="Search a name"
        placeholder="Search a name or role" />
      <Problem>{log.error}</Problem>
      <Summary day={day} csv={csvUrl("staff", { day })}>
        {log.data?.day === day && all.length > 0 ? `${log.data.present} of ${plural(all.length, "staff member", "staff")} seen` : null}
      </Summary>
      {log.loading && !log.data ? null : all.length === 0 ? (
        <Empty title="Nobody is enrolled yet" action={<Link className="btn btn-primary" to="/app/people">Go to People</Link>}>
          Enrol staff who agree on the People page. Then turn on the staff register on the camera at the staff entrance.
        </Empty>
      ) : (
        <>
          {cameras && naming.length === 0 && (
            <p className="notice">No camera has the staff register on. Open the camera at the staff entrance, choose Edit and turn it on.</p>
          )}
          {rows.length === 0 ? (
            <Empty title={`Nobody called ${search.trim()}`}>Check the name, or clear the search.</Empty>
          ) : (
            <>
              <div className="log-wide card card-flush">
                <table className="table log-table">
                  <caption className="sr-only">Staff first in and last out, {dayTitle(day)}</caption>
                  <thead>
                    <tr><th scope="col">Name</th><th scope="col">First seen</th><th scope="col">Last seen</th><th scope="col">Seen on</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.staff_id} className={r.present ? undefined : "is-absent"}>
                        <th scope="row">{r.name}<span className="cell-sub">{sentence(r.role)}{r.active ? "" : ", no longer enrolled"}</span></th>
                        <td className="num">
                          {when(seen(r).first) ?? status(r)}
                          {seen(r).cameIn && <span className="cell-sub">Came in {when(seen(r).cameIn)}</span>}
                        </td>
                        <td className="num">
                          {r.present ? when(seen(r).last) : null}
                          {seen(r).wentOut && <span className="cell-sub">Went out {when(seen(r).wentOut)}</span>}
                        </td>
                        <td>{r.cameras.join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="log-narrow list log-cards" aria-label={`Staff first in and last out, ${dayTitle(day)}`}>
                {rows.map((r) => (
                  <li key={r.staff_id} className={r.present ? undefined : "is-absent"}>
                    <div className="log-card">
                      <span><strong>{r.name}</strong> <span className="muted small">{sentence(r.role)}{r.active ? "" : ", no longer enrolled"}</span></span>
                      <span className="num">{line(r)}</span>
                      {crossed(r) && <span className="num muted">{sentence(crossed(r))}</span>}
                      {r.cameras.length > 0 && <span className="muted">{r.cameras.join(", ")}</span>}
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </>
  );
}

export default function Logs() {
  const { log = "vehicles" } = useParams();
  const cameras = useLoad(() => api.cameras(), []);
  if (!LOGS.some((l) => l.id === log)) return <Navigate to="/app/logs/vehicles" replace />;
  return (
    <Page>
      <PageHead title="Logs" sub={<span>{log === "staff" ? "When each enrolled staff member was first and last seen each day." : "Every vehicle in and out through the gates, by plate."}</span>} />
      <nav className="tabs" aria-label="Logs">
        {LOGS.map((l) => (
          <Link key={l.id} to={`/app/logs/${l.id}`} aria-current={l.id === log ? "page" : undefined}>{l.label}</Link>
        ))}
      </nav>
      <div className="stack" style={{ gap: 16, marginTop: -8 }}>
        {log === "staff" ? <StaffLog key="staff" cameras={cameras.data} /> : <GateLog key="vehicles" cameras={cameras.data} />}
      </div>
    </Page>
  );
}
