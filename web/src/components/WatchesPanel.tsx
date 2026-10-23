import { useMemo, useState } from "react";
import { api } from "../api";
import { useLoad } from "../hooks";
import { plain } from "../format";
import type { Camera, Preset, Watch, WatchRow } from "../types";
import { WatchSentence } from "./WatchSentence";

const PRESET_TITLES: Record<string, string> = {
  enters: "Comes into a zone",
  leaves: "Leaves a zone",
  stays_too_long: "Stays too long",
  too_many: "Too many at once",
  fall: "Someone falls",
  about_to_fall: "About to fall",
  possible_intruder: "Possible intruder",
  lookout: "Look out for someone",
  gate_left_open: "Left open",
};

const CLASS_SETS: Record<string, string[]> = {
  People: ["person"],
  Vehicles: ["car", "truck", "bus", "motorcycle"],
  Bicycles: ["bicycle"],
  Pets: ["dog", "cat"],
  Bags: ["backpack", "handbag", "suitcase"],
};

const DEFAULTS: Record<string, unknown> = {
  classes: "People", value: "", start: "22:00", end: "06:00", hold: 60, text: "", not_text: "", who: "", entries: [], climbs: [],
};

function fieldsOf(p: Preset): string[] {
  const found = new Set<string>();
  const walk = (n: unknown) => {
    if (typeof n === "string") for (const m of n.matchAll(/\$([a-z_]+)/g)) found.add(m[1]!);
    else if (Array.isArray(n)) n.forEach(walk);
    else if (n && typeof n === "object") Object.values(n).forEach(walk);
  };
  walk(p.watch);
  found.delete("same_as");
  return [...found];
}

function PresetForm({ preset, camera, people, onAdded }: {
  preset: Preset; camera: Camera; people: Record<string, string>; onAdded: (w: WatchRow) => void;
}) {
  const fields = useMemo(() => fieldsOf(preset), [preset]);
  const zones = camera.zones.map((z) => z.name);
  const [values, setValues] = useState<Record<string, unknown>>(() => ({
    ...DEFAULTS,
    zone: zones[0] ?? "",
    value: preset.preset === "too_many" ? 5 : 60,
    text: preset.preset === "gate_left_open" ? "an open gate" : "",
    not_text: preset.preset === "gate_left_open" ? "a closed gate" : "",
  }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: string, v: unknown) => setValues({ ...values, [k]: v });
  const needsZone = fields.includes("zone");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const fill: Record<string, unknown> = {};
    for (const f of fields) {
      const v = values[f];
      if (f === "classes") fill.classes = CLASS_SETS[v as string] ?? ["person"];
      else if (f === "value" || f === "hold") fill[f] = Number(v);
      else if (f === "who") { if (v) fill.who = v; }
      else if (f === "text") { if (v) fill.text = v; }
      else fill[f] = v;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api.addWatch(camera.id, { preset: preset.preset, fill });
      if (r.watch && r.id) onAdded({ id: r.id, camera_id: camera.id, text: preset.preset, watch: r.watch, enabled: true });
      else setError(r.problem ?? "That watch could not be added.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (needsZone && zones.length === 0) {
    return <p className="notice">This one needs a zone. Draw a zone on the picture first, then come back.</p>;
  }

  const zoneChecks = (key: string, label: string, hint: string) => (
    <fieldset className="field fieldset">
      <legend className="label">{label}</legend>
      <span className="hint">{hint}</span>
      {zones.length === 0 ? <span className="hint">Draw zones first to use this part.</span> : (
        <div className="row">
          {zones.map((z) => {
            const list = (values[key] as string[]) ?? [];
            return (
              <label key={z} className="check small">
                <input type="checkbox" checked={list.includes(z)}
                  onChange={(e) => set(key, e.target.checked ? [...list, z] : list.filter((x) => x !== z))} />
                {z}
              </label>
            );
          })}
        </div>
      )}
    </fieldset>
  );

  return (
    <form className="preset-form" onSubmit={submit}>
      <p className="small muted">{plain(preset.description)}.</p>
      <div className="preset-fields">
        {fields.includes("classes") && (
          <label className="field"><span>What</span>
            <select className="select" value={values.classes as string} onChange={(e) => set("classes", e.target.value)}>
              {Object.keys(CLASS_SETS).map((k) => <option key={k}>{k}</option>)}
            </select>
          </label>
        )}
        {needsZone && (
          <label className="field"><span>Zone</span>
            <select className="select" value={values.zone as string} onChange={(e) => set("zone", e.target.value)}>
              {zones.map((z) => <option key={z}>{z}</option>)}
            </select>
          </label>
        )}
        {fields.includes("value") && (
          <label className="field"><span>{preset.preset === "too_many" ? "How many" : "Seconds"}</span>
            <input className="input" type="number" min={1} value={values.value as number} onChange={(e) => set("value", e.target.value)} required />
          </label>
        )}
        {fields.includes("who") && (
          <label className="field"><span>Who</span>
            <select className="select" value={values.who as string} onChange={(e) => set("who", e.target.value)}>
              <option value="">Anyone matching the description</option>
              {Object.entries(people).map(([id, n]) => <option key={id} value={id}>{n}</option>)}
            </select>
          </label>
        )}
        {fields.includes("text") && (
          <label className="field"><span>{preset.preset === "lookout" ? "Description" : "Looks like"}</span>
            <input className="input" value={values.text as string} onChange={(e) => set("text", e.target.value)}
              placeholder={preset.preset === "lookout" ? "a man in a red jacket" : "an open gate"} />
          </label>
        )}
        {fields.includes("not_text") && (
          <label className="field"><span>Instead of</span>
            <input className="input" value={values.not_text as string} onChange={(e) => set("not_text", e.target.value)} />
          </label>
        )}
        {fields.includes("hold") && (
          <label className="field"><span>For how many seconds</span>
            <input className="input" type="number" min={1} value={values.hold as number} onChange={(e) => set("hold", e.target.value)} />
          </label>
        )}
        {fields.includes("start") && (
          <label className="field"><span>From</span>
            <input className="input" type="time" value={values.start as string} onChange={(e) => set("start", e.target.value)} />
          </label>
        )}
        {fields.includes("end") && (
          <label className="field"><span>Until</span>
            <input className="input" type="time" value={values.end as string} onChange={(e) => set("end", e.target.value)} />
          </label>
        )}
      </div>
      {fields.includes("entries") && zoneChecks("entries", "Ways in", "Doors, windows or gates someone might try.")}
      {fields.includes("climbs") && zoneChecks("climbs", "Places someone could climb", "Walls, gates or fences.")}
      {preset.preset === "possible_intruder" && (
        <p className="hint">This watch looks only at behaviour: lingering, trying more than one way in, climbing, coming back. Clothing, skin, age and gender are never used.</p>
      )}
      {preset.preset === "lookout" && !values.who && !values.text && (
        <p className="hint">Pick an enrolled person or describe what they wear.</p>
      )}
      {error && <p className="notice notice-error" role="alert">{plain(error)}</p>}
      <div>
        <button className="btn btn-primary" disabled={busy || (preset.preset === "lookout" && !values.who && !values.text)}>
          {busy ? "Adding" : "Add watch"}
        </button>
      </div>
    </form>
  );
}

export function WatchesPanel({ camera }: { camera: Camera }) {
  const watches = useLoad(() => api.watches(camera.id), [camera.id]);
  const presets = useLoad(() => api.presets(), []);
  const peopleList = useLoad(() => api.people(), []);
  const people = useMemo(() => Object.fromEntries((peopleList.data ?? []).map((p) => [p.person_id, p.name])), [peopleList.data]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ watch: Watch | null; problem: string | null; id?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [preset, setPreset] = useState<string | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.addWatch(camera.id, { text: text.trim() });
      setResult(r);
      if (r.watch && r.id) {
        watches.setData([...(watches.data ?? []), { id: r.id, camera_id: camera.id, text: text.trim(), watch: r.watch, enabled: true }]);
        setText("");
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function toggle(w: WatchRow) {
    setToggling(w.id);
    try {
      const updated = await api.toggleWatch(w.id, !w.enabled);
      watches.setData((watches.data ?? []).map((x) => (x.id === w.id ? { ...x, enabled: updated.enabled } : x)));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setToggling(null);
    }
  }

  const list = watches.data ?? [];
  const chosen = presets.data?.find((p) => p.preset === preset);

  return (
    <section className="section watches" aria-labelledby="watches-title">
      <div className="section-head">
        <h2 id="watches-title">Watches</h2>
        <span className="muted small">{list.filter((w) => w.enabled).length} on</span>
      </div>

      <form className="watch-add" onSubmit={add}>
        <label className="field">
          <span>Tell Watchful what to look out for</span>
          <textarea className="textarea" rows={2} value={text} onChange={(e) => setText(e.target.value)}
            placeholder="For example: tell me when someone enters the left side"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); add(e); } }} />
        </label>
        <div className="row">
          <button className="btn btn-primary" disabled={busy || !text.trim()}>{busy ? "Reading your watch" : "Add watch"}</button>
          {busy && <span className="spinner" aria-hidden="true" />}
          {camera.zones.length > 0 && <span className="hint">Zones here: {camera.zones.map((z) => z.name).join(", ")}</span>}
        </div>
      </form>

      <div aria-live="polite">
        {result?.watch && (
          <div className="compiled" data-testid="compiled-watch">
            <p className="small"><strong>Added.</strong> This is how Watchful understood it:</p>
            <WatchSentence watch={result.watch} people={people} />
          </div>
        )}
        {result && !result.watch && (
          <div className="notice notice-warn" role="alert">
            <div>
              <p><strong>That could not become a watch yet.</strong> {plain(result.problem)}</p>
              <p className="small">Try naming one of this camera's zones, or start from a ready made watch below.</p>
            </div>
          </div>
        )}
        {error && <p className="notice notice-error" role="alert">{error}</p>}
      </div>

      <details className="presets" open={preset !== null}>
        <summary>Start from a ready made watch</summary>
        <div className="row preset-chips" role="group" aria-label="Ready made watches">
          {(presets.data ?? []).map((p) => (
            <button key={p.preset} type="button" className="btn btn-sm" aria-pressed={preset === p.preset}
              onClick={() => setPreset(preset === p.preset ? null : p.preset)}>
              {PRESET_TITLES[p.preset] ?? p.preset.replace(/_/g, " ")}
            </button>
          ))}
        </div>
        {chosen && (
          <PresetForm key={chosen.preset} preset={chosen} camera={camera} people={people}
            onAdded={(w) => { watches.setData([...(watches.data ?? []), w]); setPreset(null); setResult({ watch: w.watch, problem: null, id: w.id }); }} />
        )}
      </details>

      {watches.loading && !watches.data ? <p className="muted small">Loading watches</p> : list.length === 0 ? (
        <div className="empty">
          <p>No watches on this camera yet.</p>
          <p className="small">Describe one above in your own words.</p>
        </div>
      ) : (
        <ul className="list watch-list">
          {[...list].reverse().map((w) => (
            <li key={w.id} className={w.enabled ? "" : "is-off"}>
              <div className="grow">
                <WatchSentence watch={w.watch} people={people} small />
                {w.text && !PRESET_TITLES[w.text] && w.text !== "lookout" && <p className="xsmall muted you-wrote">You wrote: {w.text}</p>}
                {w.watch.subject.same_as?.expires_at && (
                  <p className="xsmall muted">Ends on its own {new Date(w.watch.subject.same_as.expires_at * 1000).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}</p>
                )}
              </div>
              <label className="switch" title={w.enabled ? "Turn this watch off" : "Turn this watch on"}>
                <input type="checkbox" role="switch" checked={w.enabled} disabled={toggling === w.id} onChange={() => toggle(w)}
                  aria-label={`${w.watch.name}: ${w.enabled ? "on" : "off"}`} />
                <span />
              </label>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
