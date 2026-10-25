import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { Thumb } from "../components/Thumb";
import { clock, plural } from "../format";
import { useLoad, useTitle } from "../hooks";
import type { EventRow, PeopleGroup } from "../types";
import { Empty, Page, PageHead, Problem, Section } from "./ui/Page";

const KIND_WORDS: Record<string, string> = {
  enter: "came into",
  exit: "went out of",
  cross: "crossed",
  falling: "started to fall",
  fallen: "fell and stayed down",
  recovered: "got back up",
  tamper_covered: "Camera covered",
  tamper_blurred: "Camera blurred",
  tamper_moved: "Camera moved",
  tamper_restored: "Camera view back to normal",
};

function eventWords(e: EventRow): string {
  const who = e.person_label ?? (e.cls === "person" ? "Someone" : e.cls ? `A ${e.cls}` : "Something");
  if (e.kind.startsWith("tamper")) return KIND_WORDS[e.kind] ?? e.kind;
  if (e.kind === "enter" || e.kind === "exit" || e.kind === "cross") return `${who} ${KIND_WORDS[e.kind]} ${e.zone ?? "a zone"}`;
  return `${who} ${KIND_WORDS[e.kind] ?? e.kind.replace(/_/g, " ")}`;
}

function ticks(t0: number, t1: number): number[] {
  const steps = [60, 300, 600, 1800, 3600, 7200, 21600];
  const step = steps.find((s) => (t1 - t0) / s <= 5) ?? 43200;
  const out: number[] = [];
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) out.push(t);
  return out;
}

function Lanes({ people, names }: { people: PeopleGroup[]; names: Map<string, string> }) {
  const t0 = Math.min(...people.map((p) => p.first_ts));
  const t1 = Math.max(...people.map((p) => p.last_ts), t0 + 60);
  const pad = (t1 - t0) * 0.03;
  const [lo, hi] = [t0 - pad, t1 + pad];
  const x = (t: number) => `${((t - lo) / (hi - lo)) * 100}%`;
  return (
    <figure className="lanes" aria-label="When each person was in view">
      <div className="lane-axis" aria-hidden="true">
        <span />
        <div className="lane-track">{ticks(lo, hi).map((t) => <span key={t} className="tick num" style={{ left: x(t) }}>{clock(t)}</span>)}</div>
      </div>
      <ul className="list">
        {people.slice(0, 24).map((p) => (
          <li key={p.group_id} className="lane">
            <span className="lane-who">
              <Thumb src={p.thumbnail_url} alt="" w={30} h={30} label="" />
              <span>{names.get(p.group_id)}{p.ambiguous && <span className="sr-only">, may be mixed up with someone who looks alike</span>}</span>
            </span>
            <div className="lane-track">
              {ticks(lo, hi).map((t) => <span key={t} className="grid-line" style={{ left: x(t) }} />)}
              {(p.tracks?.length ? p.tracks : [{ track_id: -1, first_ts: p.first_ts, last_ts: p.last_ts }]).map((t) => (
                <span key={t.track_id} className="lane-bar" style={{ left: x(t.first_ts), width: `max(4px, calc(${x(t.last_ts)} - ${x(t.first_ts)}))` }} />
              ))}
              {p.visits.map((v) => (
                <span key={v.event_id} className={`lane-mark ${v.kind === "enter" ? "in" : "out"}`} style={{ left: x(v.ts) }}
                  title={`${v.kind === "enter" ? "Came into" : "Went out of"} ${v.zone} at ${clock(v.ts)}`} />
              ))}
            </div>
          </li>
        ))}
      </ul>
      <figcaption className="small muted">
        Each row is one person. <span className="mark-key" aria-hidden="true" /> came into a zone, <span className="mark-key out" aria-hidden="true" /> went out.
      </figcaption>
    </figure>
  );
}

// Who came and went on one camera, read from the event log OpenCV writes as it watches.
export default function History() {
  const { id = "" } = useParams();
  const camera = useLoad(() => api.camera(id), [id]);
  const timeline = useLoad(() => api.timeline(id), [id], 30_000);
  const [everything, setEverything] = useState(false);
  const name = camera.data?.name ?? "Camera";
  useTitle(`${name}, who came and went`);

  const people = useMemo(() => [...(timeline.data?.people ?? [])].sort((a, b) => a.first_ts - b.first_ts), [timeline.data]);
  const names = useMemo(() => new Map(people.map((p, i) => [p.group_id, p.label ?? `Person ${i + 1}`])), [people]);
  const events = [...(timeline.data?.events ?? [])].sort((a, b) => b.ts - a.ts);

  return (
    <Page>
      <PageHead title="Who came and went" back={{ to: `/app/cameras/${id}`, label: name }}
        sub={<span>People are told apart by face when enrolled, otherwise by clothing and timing.</span>} />
      <Problem>{timeline.error}</Problem>
      {timeline.loading && !timeline.data ? <p className="muted">Reading the event log</p> : (
        <>
          <Section title="People" id="people-title" aside={<span className="small muted">{plural(people.length, "person", "people")} seen</span>}>
            {people.length === 0 ? (
              <Empty title="Nobody seen yet">People show here once this camera sees someone.</Empty>
            ) : (
              <Lanes people={people} names={names} />
            )}
          </Section>
          {events.length > 0 && (
            <Section title="Every event" id="events-title">
              <ol className="list events card divided" style={{ padding: "4px 16px" }}>
                {(everything ? events.slice(0, 300) : events.slice(0, 12)).map((e) => (
                  <li key={e.id}>
                    <time className="event-time num" dateTime={new Date(e.ts * 1000).toISOString()}>{clock(e.ts)}</time>
                    <span className="grow">{eventWords(e)}</span>
                  </li>
                ))}
              </ol>
              {!everything && events.length > 12 && (
                <div><button className="btn btn-quiet btn-sm" onClick={() => setEverything(true)}>Show {Math.min(events.length, 300) - 12} more</button></div>
              )}
            </Section>
          )}
        </>
      )}
    </Page>
  );
}
