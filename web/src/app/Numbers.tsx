import { api } from "../api";
import { count } from "../format";
import { useLoad } from "../hooks";
import { Problem } from "./ui/Page";
import { Table } from "./ui/Table";

function Figure({ value }: { value: string | null }) {
  return value === null ? <p className="figure-none">None yet</p> : <p className="figure-value num">{value}</p>;
}

// For admins: what OpenCV and the AI are doing, measured as it runs.
export function Numbers() {
  const m = useLoad(() => api.metrics(), [], 5000);
  const precision = useLoad(() => api.precision(), []);
  const data = m.data;
  const frames = data?.cameras.reduce((a, c) => a + (c.status.stats.frames ?? 0), 0) ?? 0;
  const skipped = data?.cameras.reduce((a, c) => a + (c.status.stats.frames_skipped ?? 0), 0) ?? 0;
  const labelled = Object.values(precision.data ?? {}).reduce(
    (a, r) => ({ real: a.real + (r.acknowledged ?? 0), false: a.false + (r.false_alarm ?? 0) }), { real: 0, false: 0 });

  return (
    <>
      <h1>Numbers</h1>
      <p className="lede" style={{ marginTop: -16 }}>Measured as it runs. Counts start when a camera's analysis starts.</p>
      <Problem>{m.error}</Problem>
      {data && (
        <>
          <div className="figures">
            <div className="card figure">
              <Figure value={frames ? `${Math.round((skipped / frames) * 100)}%` : null} />
              <p>of frames skipped by the motion check, so the detector runs only when something moves</p>
            </div>
            <div className="card figure">
              <Figure value={count(data.ai_calls_today)} />
              <p>AI checks today, of {count(data.ai_daily_limit)} a day. The AI sees only moments OpenCV flags.</p>
            </div>
            <div className="card figure">
              <Figure value={labelled.real + labelled.false ? `${Math.round((labelled.real / (labelled.real + labelled.false)) * 100)}%` : null} />
              <p>of incidents people marked were real, from {count(labelled.real + labelled.false)} acknowledged or marked false</p>
            </div>
          </div>
          <section className="section" aria-labelledby="per-camera">
            <h2 id="per-camera" className="h2">Per camera</h2>
            <div className="card" style={{ padding: "8px 20px" }}>
              <Table caption="Per camera" head={["Camera", "Frames a second", "Frames analysed", "Skipped", "Per frame"]}>
                {data.cameras.map((c) => {
                  const s = c.status.stats;
                  return (
                    <tr key={c.id}>
                      <th scope="row" style={{ textAlign: "left" }}>{c.name}</th>
                      <td className="num">{c.status.running ? c.status.fps.toFixed(1) : "Not running"}</td>
                      <td className="num">{s.frames != null ? count(s.frames) : ""}</td>
                      <td className="num">{s.frames ? `${Math.round(((s.frames_skipped ?? 0) / s.frames) * 100)}%` : ""}</td>
                      <td className="num">{s.ms_per_frame != null ? `${s.ms_per_frame.toFixed(0)} ms` : ""}</td>
                    </tr>
                  );
                })}
              </Table>
            </div>
          </section>
        </>
      )}
    </>
  );
}
