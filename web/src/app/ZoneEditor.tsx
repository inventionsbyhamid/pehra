import { useEffect, useRef, useState } from "react";
import type { Point, Zone } from "../types";

export type Want = { name: string; kind: "area" | "line" };

type Props = {
  background: string | null;
  zones: Zone[];
  want: Want | null; // what a watch asked for: the editor starts on drawing it
  saving: boolean;
  error: string | null;
  onSave: (zones: Zone[]) => void;
  onCancel: () => void;
};

const round = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 1000) / 1000;

// Zones are shapes on the picture; a line is two points, its inside on the right of the first point to the second.
export function ZoneEditor({ background, zones: initial, want, saving, error, onSave, onCancel }: Props) {
  const [zones, setZones] = useState<Zone[]>(initial);
  const [mode, setMode] = useState<"area" | "line">(want?.kind ?? "area");
  const [draft, setDraft] = useState<Point[]>([]);
  const [pending, setPending] = useState<{ points: Point[]; kind: "area" | "line" } | null>(null);
  const [name, setName] = useState("");
  const [size, setSize] = useState<[number, number]>([960, 540]);
  const [imgFailed, setImgFailed] = useState(false);
  const svg = useRef<SVGSVGElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const [W, H] = size;
  const font = Math.max(W, H) / 42;
  const wanted = want && !zones.some((z) => z.name.toLowerCase() === want.name.toLowerCase()) ? want : null;

  useEffect(() => {
    if (pending) nameInput.current?.focus();
  }, [pending]);

  function finish(points: Point[], kind = mode) {
    if (points.length < (kind === "line" ? 2 : 3)) return;
    setPending({ points, kind });
    setDraft([]);
    setName(wanted && wanted.kind === kind ? wanted.name : "");
  }

  function addPoint(e: React.MouseEvent<SVGSVGElement>) {
    if (pending) return;
    const r = svg.current!.getBoundingClientRect();
    const p: Point = [round((e.clientX - r.left) / r.width), round((e.clientY - r.top) / r.height)];
    if (mode === "line") {
      const next = [...draft, p];
      if (next.length === 2) finish(next, "line");
      else setDraft(next);
      return;
    }
    if (draft.length >= 3) {
      const [fx, fy] = draft[0]!;
      if (Math.hypot((fx - p[0]) * W, (fy - p[1]) * H) < Math.max(W, H) * 0.025) {
        finish(draft);
        return;
      }
    }
    setDraft([...draft, p]);
  }

  function keys(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      setDraft([]);
      setPending(null);
    } else if (e.key === "Enter" && draft.length >= 3 && !pending && mode === "area") {
      e.preventDefault();
      finish(draft);
    }
  }

  function addZone(e: React.FormEvent) {
    e.preventDefault();
    const n = name.trim();
    if (!pending || !n) return;
    setZones([...zones, { name: n, polygon: pending.points, kind: pending.kind }]);
    setPending(null);
    setName("");
  }

  const nameTaken = zones.some((z) => z.name.toLowerCase() === name.trim().toLowerCase());
  const pts = (poly: Point[]) => poly.map(([x, y]) => `${x * W},${y * H}`).join(" ");

  function line(points: Point[], label?: string) {
    const [[ax, ay], [bx, by]] = [points[0]!, points[1]!];
    const [x1, y1, x2, y2] = [ax * W, ay * H, bx * W, by * H];
    const len = Math.hypot(x2 - x1, y2 - y1) || 1;
    const [nx, ny] = [-(y2 - y1) / len, (x2 - x1) / len];
    const [mx, my] = [(x1 + x2) / 2, (y1 + y2) / 2];
    const tip = [mx + nx * font * 2.2, my + ny * font * 2.2];
    return (
      <g>
        <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#fff" strokeWidth={font / 5} strokeLinecap="round" />
        <line x1={mx} y1={my} x2={tip[0]} y2={tip[1]} stroke="#fff" strokeWidth={font / 8} markerEnd="url(#arrow)" />
        <text x={tip[0]! + nx * font * 0.8} y={tip[1]! + ny * font * 0.8 + font / 3} fontSize={font * 0.9} fill="#fff"
          textAnchor="middle" stroke="rgb(0 0 0 / 0.6)" strokeWidth={font / 8} paintOrder="stroke" fontWeight={700}>in</text>
        {label && (
          <text x={x1 + font / 2} y={y1 - font / 2} fontSize={font} fill="#fff" stroke="rgb(0 0 0 / 0.6)"
            strokeWidth={font / 8} paintOrder="stroke" fontWeight={700}>{label}</text>
        )}
      </g>
    );
  }

  const help = pending ? "" : mode === "line"
    ? draft.length === 0 ? "Click where the line starts, then where it ends. Inside is on the right of the arrow's start." : "Now click where the line ends."
    : draft.length === 0 ? "Click the corners of the area on the picture. Click the first corner again to close it."
    : draft.length < 3 ? `Corner ${draft.length} placed. Keep clicking around the area.` : "Click the first corner, or press Close the shape.";

  return (
    <div className="zone-editor" onKeyDown={keys}>
      {wanted && !pending && (
        <p className="notice" role="status">
          {wanted.kind === "line" ? `Draw the ${wanted.name} line where things cross.` : `Draw the ${wanted.name} zone.`} A watch needs it.
        </p>
      )}
      <div className="zone-canvas" style={{ aspectRatio: `${W} / ${H}` }}>
        {background && !imgFailed ? (
          <img src={background} alt="" onLoad={(e) => setSize([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])}
            onError={() => setImgFailed(true)} />
        ) : (
          <div className="stage-msg"><p>No picture from this camera yet. You can still draw, or come back once it is live.</p></div>
        )}
        <svg ref={svg} viewBox={`0 0 ${W} ${H}`} onClick={addPoint} role="application" tabIndex={0}
          aria-label={mode === "line" ? "Line drawing area. Click the start, then the end of the line." : "Zone drawing area. Click to add corners, click the first corner or press Enter to close, Escape to cancel."}>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
              <path d="M0 0L10 5L0 10z" fill="#fff" />
            </marker>
          </defs>
          {zones.map((z, i) => z.kind === "line" && z.polygon.length >= 2 ? (
            <g key={z.id ?? `new${i}`}>{line(z.polygon, z.name)}</g>
          ) : (
            <g key={z.id ?? `new${i}`}>
              <polygon points={pts(z.polygon)} fill="#fff" fillOpacity={0.14} stroke="#fff" strokeWidth={font / 6} />
              <text x={z.polygon[0]![0] * W + font / 2} y={z.polygon[0]![1] * H + font * 1.3} fontSize={font} fill="#fff"
                stroke="rgb(0 0 0 / 0.6)" strokeWidth={font / 8} paintOrder="stroke" fontWeight={700}>{z.name}</text>
            </g>
          ))}
          {pending && pending.kind === "line" && line(pending.points)}
          {pending && pending.kind === "area" && (
            <polygon points={pts(pending.points)} fill="#fff" fillOpacity={0.25} stroke="#fff" strokeWidth={font / 6}
              strokeDasharray={`${font / 2} ${font / 3}`} />
          )}
          {draft.length > 0 && (
            <g>
              <polyline points={pts(draft)} fill="none" stroke="#fff" strokeWidth={font / 6} />
              {draft.map(([x, y], i) => (
                <circle key={i} cx={x * W} cy={y * H} r={i === 0 ? font / 2.4 : font / 3.2} fill="#fff" stroke="#000"
                  strokeOpacity={0.5} strokeWidth={font / 14} />
              ))}
            </g>
          )}
        </svg>
      </div>

      <div className="zone-tools">
        {pending ? (
          <form className="row" style={{ alignItems: "flex-end" }} onSubmit={addZone}>
            <label className="field grow" style={{ minWidth: 220 }}>
              <span>{pending.kind === "line" ? "Name this line" : "Name this zone"}</span>
              <input ref={nameInput} className="input" value={name} onChange={(e) => setName(e.target.value)}
                placeholder={pending.kind === "line" ? "For example: gate" : "For example: pool"} maxLength={40} />
            </label>
            <button className="btn btn-primary" disabled={!name.trim() || nameTaken}>Keep it</button>
            {pending.kind === "line" && (
              <button type="button" className="btn" onClick={() => setPending({ ...pending, points: [...pending.points].reverse() })}>
                Swap inside and outside
              </button>
            )}
            <button type="button" className="btn btn-quiet" onClick={() => setPending(null)}>Draw again</button>
            {nameTaken && <p className="hint" style={{ width: "100%" }}>There is already one with that name.</p>}
          </form>
        ) : (
          <div className="stack">
            {!want && (
              <div className="segmented" role="group" aria-label="What to draw">
                <button type="button" aria-pressed={mode === "area"} onClick={() => { setMode("area"); setDraft([]); }}>Zone</button>
                <button type="button" aria-pressed={mode === "line"} onClick={() => { setMode("line"); setDraft([]); }}>Line</button>
              </div>
            )}
            <p className="small">{help}</p>
            {draft.length > 0 && (
              <div className="row">
                {mode === "area" && <button type="button" className="btn btn-sm btn-primary" disabled={draft.length < 3} onClick={() => finish(draft)}>Close the shape</button>}
                <button type="button" className="btn btn-sm" onClick={() => setDraft(draft.slice(0, -1))}>Undo</button>
                <button type="button" className="btn btn-sm btn-quiet" onClick={() => setDraft([])}>Start over</button>
              </div>
            )}
          </div>
        )}

        {zones.length > 0 && (
          <div className="stack" style={{ gap: 8 }}>
            <h3 className="h3">On this camera</h3>
            <ul className="list zone-list">
              {zones.map((z, i) => (
                <li key={z.id ?? `new${i}`}>
                  <span className="zone-kind">{z.kind === "line" ? "Line" : "Zone"}</span>
                  <input className="input" aria-label={`Name of ${z.kind === "line" ? "line" : "zone"} ${i + 1}`} value={z.name} maxLength={40}
                    onChange={(e) => setZones(zones.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
                  <button type="button" className="btn btn-sm btn-danger" onClick={() => setZones(zones.filter((_, j) => j !== i))}>
                    Remove<span className="sr-only"> {z.name}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {error && <p className="notice notice-error" role="alert">{error}</p>}
        <div className="row">
          <button type="button" className="btn btn-primary" disabled={saving || zones.some((z) => !z.name.trim())}
            onClick={() => onSave(zones.map((z) => ({ ...z, name: z.name.trim() })))}>
            {saving ? "Saving" : "Save"}
          </button>
          <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        </div>
        <p className="hint">Watches use these by name, so rename with care. Saving restarts this camera's analysis for a moment.</p>
      </div>
    </div>
  );
}
