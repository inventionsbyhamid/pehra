import { useEffect, useRef, useState } from "react";
import type { Point, Zone } from "../types";

const QUICK: { label: string; name: string; polygon: Point[] }[] = [
  { label: "Left half", name: "left", polygon: [[0, 0], [0.5, 0], [0.5, 1], [0, 1]] },
  { label: "Right half", name: "right", polygon: [[0.5, 0], [1, 0], [1, 1], [0.5, 1]] },
  { label: "Top half", name: "top", polygon: [[0, 0], [1, 0], [1, 0.5], [0, 0.5]] },
  { label: "Bottom half", name: "bottom", polygon: [[0, 0.5], [1, 0.5], [1, 1], [0, 1]] },
  { label: "Whole view", name: "whole view", polygon: [[0, 0], [1, 0], [1, 1], [0, 1]] },
];

export const zoneColor = (i: number) => `var(--zone-${(i % 4) + 1})`;

type Props = {
  background: string | null;
  zones: Zone[];
  saving: boolean;
  error: string | null;
  onSave: (zones: Zone[]) => void;
  onCancel: () => void;
};

export function ZoneEditor({ background, zones: initial, saving, error, onSave, onCancel }: Props) {
  const [zones, setZones] = useState<Zone[]>(initial);
  const [draft, setDraft] = useState<Point[]>([]);
  const [pending, setPending] = useState<Point[] | null>(null);
  const [name, setName] = useState("");
  const [size, setSize] = useState<[number, number]>([960, 540]);
  const [imgFailed, setImgFailed] = useState(false);
  const svg = useRef<SVGSVGElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const [W, H] = size;

  useEffect(() => {
    if (pending) nameInput.current?.focus();
  }, [pending]);

  function finish(points: Point[], suggested = "") {
    if (points.length < 3) return;
    setPending(points);
    setDraft([]);
    setName(suggested && !zones.some((z) => z.name === suggested) ? suggested : "");
  }

  function addPoint(e: React.MouseEvent<SVGSVGElement>) {
    if (pending) return;
    const r = svg.current!.getBoundingClientRect();
    const p: Point = [
      Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    ];
    if (draft.length >= 3) {
      const [fx, fy] = draft[0]!;
      if (Math.hypot((fx - p[0]) * W, (fy - p[1]) * H) < Math.max(W, H) * 0.025) {
        finish(draft);
        return;
      }
    }
    setDraft([...draft, [Math.round(p[0] * 1000) / 1000, Math.round(p[1] * 1000) / 1000]]);
  }

  function keys(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      setDraft([]);
      setPending(null);
    } else if (e.key === "Enter" && draft.length >= 3 && !pending) {
      e.preventDefault();
      finish(draft);
    } else if ((e.key === "Backspace" || e.key === "Delete") && draft.length && !pending) {
      setDraft(draft.slice(0, -1));
    }
  }

  function addZone(e: React.FormEvent) {
    e.preventDefault();
    const n = name.trim();
    if (!pending || !n) return;
    setZones([...zones, { name: n, polygon: pending }]);
    setPending(null);
    setName("");
  }

  const nameTaken = zones.some((z) => z.name.toLowerCase() === name.trim().toLowerCase());
  const pts = (poly: Point[]) => poly.map(([x, y]) => `${x * W},${y * H}`).join(" ");
  const font = Math.max(W, H) / 42;

  return (
    <div className="zone-editor" onKeyDown={keys}>
      <div className="zone-canvas" style={{ aspectRatio: `${W} / ${H}` }}>
        {background && !imgFailed ? (
          <img src={background} alt="" onLoad={(e) => setSize([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])}
            onError={() => setImgFailed(true)} />
        ) : (
          <div className="stage-msg"><p>No picture from this camera yet. You can still draw zones, or wait for the camera and try again.</p></div>
        )}
        <svg ref={svg} viewBox={`0 0 ${W} ${H}`} onClick={addPoint} role="application" tabIndex={0}
          aria-label="Zone drawing area. Click to add corners, click the first corner or press Enter to finish, Escape to cancel.">
          {zones.map((z, i) => (
            <g key={z.id ?? `new${i}`} style={{ color: zoneColor(i) }}>
              <polygon points={pts(z.polygon)} fill="currentColor" fillOpacity={0.18} stroke="currentColor" strokeWidth={font / 6} />
              <text x={z.polygon[0]![0] * W + font / 2} y={z.polygon[0]![1] * H + font * 1.3} fontSize={font} fill="#fff"
                stroke="rgb(0 0 0 / 0.6)" strokeWidth={font / 8} paintOrder="stroke" fontWeight={700}>{z.name}</text>
            </g>
          ))}
          {pending && (
            <polygon points={pts(pending)} fill="var(--accent)" fillOpacity={0.25} stroke="var(--accent-ink)" strokeWidth={font / 6} strokeDasharray={`${font / 2} ${font / 3}`} />
          )}
          {draft.length > 0 && (
            <g>
              <polyline points={pts(draft)} fill="none" stroke="#fff" strokeWidth={font / 6} />
              {draft.map(([x, y], i) => (
                <circle key={i} cx={x * W} cy={y * H} r={i === 0 ? font / 2.4 : font / 3.2} fill={i === 0 ? "var(--accent)" : "#fff"}
                  stroke="#000" strokeOpacity={0.5} strokeWidth={font / 14} />
              ))}
            </g>
          )}
        </svg>
      </div>

      <div className="zone-tools">
        {pending ? (
          <form className="zone-name" onSubmit={addZone}>
            <label className="field grow">
              <span>Name this zone</span>
              <input ref={nameInput} className="input" value={name} onChange={(e) => setName(e.target.value)}
                placeholder="For example: kitchen door, parking lot, bed" maxLength={40} />
            </label>
            <div className="row">
              <button className="btn btn-primary" disabled={!name.trim() || nameTaken}>Add zone</button>
              <button type="button" className="btn" onClick={() => setPending(null)}>Discard shape</button>
            </div>
            {nameTaken && <p className="hint">There is already a zone with that name.</p>}
          </form>
        ) : (
          <div className="stack">
            <p className="small">
              {draft.length === 0 ? "Click on the picture to place the corners of a zone, or start from a quick shape."
                : draft.length < 3 ? `Corner ${draft.length} placed. Keep clicking to outline the area.`
                : "Click the first corner or press Finish to close the shape."}
            </p>
            <div className="row">
              {draft.length > 0 ? (
                <>
                  <button type="button" className="btn btn-primary btn-sm" disabled={draft.length < 3} onClick={() => finish(draft)}>Finish shape</button>
                  <button type="button" className="btn btn-sm" onClick={() => setDraft(draft.slice(0, -1))}>Undo corner</button>
                  <button type="button" className="btn btn-sm" onClick={() => setDraft([])}>Start over</button>
                </>
              ) : (
                QUICK.map((q) => (
                  <button key={q.label} type="button" className="btn btn-sm" onClick={() => finish(q.polygon, q.name)}>{q.label}</button>
                ))
              )}
            </div>
          </div>
        )}

        <div className="stack">
          <h3 className="h3">Zones on this camera</h3>
          {zones.length === 0 ? (
            <p className="muted small">No zones yet. Watches can still look at the whole view.</p>
          ) : (
            <ul className="list zone-list">
              {zones.map((z, i) => (
                <li key={z.id ?? `new${i}`}>
                  <span className="swatch" style={{ background: zoneColor(i) }} aria-hidden="true" />
                  <input className="input" aria-label={`Name of zone ${i + 1}`} value={z.name} maxLength={40}
                    onChange={(e) => setZones(zones.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                  <button type="button" className="btn btn-sm btn-danger" onClick={() => setZones(zones.filter((_, j) => j !== i))}>
                    Remove<span className="sr-only"> {z.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {error && <p className="notice notice-error" role="alert">{error}</p>}
        <div className="row">
          <button type="button" className="btn btn-primary" disabled={saving || zones.some((z) => !z.name.trim())}
            onClick={() => onSave(zones.map((z) => ({ ...z, name: z.name.trim() })))}>
            {saving ? "Saving zones" : "Save zones"}
          </button>
          <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        </div>
        <p className="hint">Saving restarts this camera's analysis for a moment. Watches use zones by name, so rename with care.</p>
      </div>
    </div>
  );
}
