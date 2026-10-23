import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { EnrolFromSighting, WatchAgainButton } from "../components/PersonActions";
import { Thumb } from "../components/Thumb";
import { zoneColor } from "../components/ZoneEditor";
import { dayLabel, plural, span, timeOf } from "../format";
import { useLoad, useTitle } from "../hooks";
import type { EventRow, PeopleGroup } from "../types";

const KIND_WORDS: Record<string, string> = {
  enter: "came into",
  exit: "went out of",
  falling: "started to fall",
  fallen: "fell and stayed down",
  recovered: "got back up",
  tamper_covered: "Camera covered",
  tamper_blurred: "Camera blurred",
  tamper_moved: "Camera moved",
  tamper_restored: "Camera view back to normal",
};

const FILTERS: { id: string; label: string; kinds: string[] }[] = [
  { id: "all", label: "Everything", kinds: [] },
  { id: "zones", label: "Comings and goings", kinds: ["enter", "exit"] },
  { id: "falls", label: "Falls", kinds: ["falling", "fallen", "recovered"] },
  { id: "tamper", label: "Camera problems", kinds: ["tamper_covered", "tamper_blurred", "tamper_moved", "tamper_restored"] },
];

function eventWords(e: EventRow): string {
  const who = e.person_label ?? (e.cls === "person" ? "Someone" : e.cls ? `A ${e.cls}` : "Something");
  if (e.kind.startsWith("tamper")) return KIND_WORDS[e.kind] ?? e.kind;
  if (e.kind === "enter" || e.kind === "exit") return `${who} ${KIND_WORDS[e.kind]} ${e.zone ?? "a zone"}`;
  return `${who} ${KIND_WORDS[e.kind] ?? e.kind.replace(/_/g, " ")}`;
}

function ticks(t0: number, t1: number): number[] {
  const steps = [10, 30, 60, 120, 300, 600, 1800, 3600, 7200, 21600];
  const step = steps.find((s) => (t1 - t0) / s <= 6) ?? 43200;
  const out: number[] = [];
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) out.push(t);
  return out;
}

function Swimlanes({ people, zones, names }: { people: PeopleGroup[]; zones: string[]; names: Map<string, string> }) {
  const [showAll, setShowAll] = useState(false);
  const t0 = Math.min(...people.map((p) => p.first_ts));
  const t1 = Math.max(...people.map((p) => p.last_ts), t0 + 10);
  const pad = (t1 - t0) * 0.02;
  const lo = t0 - pad;
  const hi = t1 + pad;
  const x = (t: number) => `${((t - lo) / (hi - lo)) * 100}%`;
  const rows = showAll ? people : people.slice(0, 24);
  const zoneIndex = (z: string) => Math.max(0, zones.indexOf(z));

  return (
    <figure className="lanes">
      <figcaption className="spread">
        <span className="small">Each row is one person. The bar runs from when they were first seen to when they were last seen.</span>
        <span className="lane-legend small">
          {zones.map((z) => (
            <span key={z} className="row" style={{ gap: 5 }}><span className="swatch" style={{ background: zoneColor(zoneIndex(z)) }} />{z}</span>
          ))}
          <span className="row" style={{ gap: 5 }}><span className="mark-key in" />came in</span>
          <span className="row" style={{ gap: 5 }}><span className="mark-key out" />went out</span>
        </span>
      </figcaption>
      <div className="lane-axis" aria-hidden="true">
        <span />
        <div className="lane-track">
          {ticks(lo, hi).map((t) => <span key={t} className="tick num" style={{ left: x(t) }}>{timeOf(t, (hi - lo) < 600)}</span>)}
        </div>
      </div>
      <ul className="list lane-rows">
        {rows.map((p) => (
          <li key={p.group_id} className="lane">
            <a href={`#${p.group_id}`} className="lane-who">
              <Thumb src={p.thumbnail_url} alt="" w={40} h={30} label="" />
              <span>{names.get(p.group_id)}</span>
            </a>
            <div className="lane-track">
              {ticks(lo, hi).map((t) => <span key={t} className="grid" style={{ left: x(t) }} />)}
              <span className="lane-bar" style={{ left: x(p.first_ts), width: `max(4px, calc(${x(p.last_ts)} - ${x(p.first_ts)}))` }} />
              {p.visits.map((v) => (
                <button key={v.event_id} type="button" className={`lane-mark ${v.kind === "enter" ? "in" : "out"}`}
                  style={{ left: x(v.ts), color: zoneColor(zoneIndex(v.zone)) }}
                  aria-label={`${names.get(p.group_id)} ${v.kind === "enter" ? "came into" : "went out of"} ${v.zone} at ${timeOf(v.ts)}`}>
                  <span className="lane-tip">{v.kind === "enter" ? "Came into" : "Went out of"} {v.zone}<br /><span className="num">{timeOf(v.ts)}</span></span>
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
      {people.length > rows.length && (
        <button className="btn btn-sm" onClick={() => setShowAll(true)}>Show all {people.length} people</button>
      )}
    </figure>
  );
}

export default function TimelinePage() {
  const { id = "" } = useParams();
  const timeline = useLoad(() => api.timeline(id), [id], 30000);
  const cameras = useLoad(() => api.cameras(), []);
  const [filter, setFilter] = useState("all");
  const cam = cameras.data?.find((c) => c.id === id);
  useTitle(cam ? `${cam.name} timeline` : "Timeline");

  const people = useMemo(() => [...(timeline.data?.people ?? [])].sort((a, b) => a.first_ts - b.first_ts), [timeline.data]);
  const names = useMemo(() => new Map(people.map((p, i) => [p.group_id, p.label ?? `Person ${i + 1}`])), [people]);
  const zones = useMemo(() => {
    const fromCam = cam?.zones.map((z) => z.name) ?? [];
    const seen = people.flatMap((p) => p.visits.map((v) => v.zone));
    return [...new Set([...fromCam, ...seen])];
  }, [cam, people]);
  const kinds = FILTERS.find((f) => f.id === filter)!.kinds;
  const events = (timeline.data?.events ?? []).filter((e) => !kinds.length || kinds.includes(e.kind)).sort((a, b) => b.ts - a.ts);
  const eventsByTrack = useMemo(() => {
    const m = new Map<number, EventRow>();
    for (const e of timeline.data?.events ?? []) if (e.track_id != null && !m.has(e.track_id)) m.set(e.track_id, e);
    return m;
  }, [timeline.data]);
  const lookAlikes = people.filter((p) => p.ambiguous).length;

  function sightingFor(p: PeopleGroup): string | null {
    return p.visits[0]?.event_id ?? p.track_ids.map((t) => eventsByTrack.get(t)?.id).find(Boolean) ?? null;
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <Link to={`/cameras/${id}`} className="small back">{cam?.name ?? "Camera"}</Link>
          <h1>Timeline</h1>
          <p className="lede">Who came and went, read from the event log OpenCV writes as it watches. People are grouped by face when enrolled, otherwise by clothing and timing.</p>
        </div>
      </div>

      {timeline.error && <p className="notice notice-error" role="alert">{timeline.error}</p>}
      {timeline.loading && !timeline.data ? <p className="muted">Loading the timeline</p> : (
        <>
          <section className="section" aria-labelledby="people-title">
            <div className="section-head">
              <h2 id="people-title">People</h2>
              <span className="small muted">{plural(people.length, "person", "people")} seen
                {lookAlikes > 0 && <>, {plural(lookAlikes, "look alike")} flagged</>}</span>
            </div>
            {people.length === 0 ? (
              <div className="empty"><p>Nobody has been seen on this camera yet.</p></div>
            ) : (
              <>
                <Swimlanes people={people} zones={zones} names={names} />
                <ul className="list people-cards">
                  {people.map((p) => (
                    <li key={p.group_id} id={p.group_id} className="person-card">
                      <Thumb src={p.thumbnail_url} alt={`Best picture of ${names.get(p.group_id)}`} w={96} h={128} />
                      <div className="stack grow" style={{ gap: 6 }}>
                        <div className="row" style={{ gap: 8 }}>
                          <h3 className="h3">{names.get(p.group_id)}</h3>
                          {p.ambiguous && <span className="tag tag-warning" title="Two people here look alike, so their times may be mixed up">Looks like someone else</span>}
                        </div>
                        <p className="small num">{dayLabel(p.first_ts) !== "Today" && `${dayLabel(p.first_ts)} `}{span(p.first_ts, p.last_ts)}</p>
                        {p.visits.length > 0 ? (
                          <ul className="list visits">
                            {p.visits.map((v) => (
                              <li key={v.event_id} className="small">
                                <span className="swatch" style={{ background: zoneColor(Math.max(0, zones.indexOf(v.zone))) }} aria-hidden="true" />
                                {v.kind === "enter" ? "Came into" : "Went out of"} {v.zone} <span className="num muted">{timeOf(v.ts)}</span>
                              </li>
                            ))}
                          </ul>
                        ) : <p className="xsmall muted">No zone visits.</p>}
                        <div className="row" style={{ gap: 6 }}>
                          <WatchAgainButton eventId={sightingFor(p)} cameras={cameras.data ?? []} defaultCamera={id} />
                          {!p.label && <EnrolFromSighting eventId={sightingFor(p)} onDone={() => timeline.reload()} />}
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>

          <section className="section" aria-labelledby="events-title">
            <div className="section-head">
              <h2 id="events-title">Events</h2>
              <div className="segmented" role="tablist" aria-label="Which events">
                {FILTERS.map((f) => <button key={f.id} role="tab" aria-selected={filter === f.id} onClick={() => setFilter(f.id)}>{f.label}</button>)}
              </div>
            </div>
            {events.length === 0 ? <div className="empty"><p>No events of this kind yet.</p></div> : (
              <ol className="list events divided">
                {events.slice(0, 300).map((e) => (
                  <li key={e.id} className="event">
                    <time className="num event-time" dateTime={new Date(e.ts * 1000).toISOString()}>{timeOf(e.ts)}</time>
                    <Thumb src={e.thumbnail_url} alt="" w={56} h={42} label="" />
                    <span className="grow">{eventWords(e)}</span>
                    {e.track_id != null && <span className="xsmall muted num">track {e.track_id}</span>}
                  </li>
                ))}
              </ol>
            )}
          </section>
        </>
      )}
    </div>
  );
}
