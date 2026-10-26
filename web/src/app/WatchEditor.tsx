import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { plain } from "../format";
import { useLoad, useTitle } from "../hooks";
import brand from "../site/brand";
import type { Severity, Watch, WatchCard } from "../types";
import { useCan, useSession } from "./session";
import { parseTask } from "./setup";
import { Fact, Facts } from "./ui/Card";
import { Choices } from "./ui/Choices";
import { Page, PageHead, Problem } from "./ui/Page";
import { purposeLabel, sentence, SEVERITY_HELP, TIER_WORDS } from "./words";

const SEVERITIES: { value: Severity; label: string }[] = [
  { value: "critical", label: "Critical" },
  { value: "warning", label: "Warning" },
  { value: "info", label: "Info" },
];

function TheCard({ card, cameraId, onInstead, onSaved }: {
  card: WatchCard & { text: string };
  cameraId: string;
  onInstead: (text: string) => void;
  onSaved: () => void;
}) {
  const cameras = useLoad(() => api.cameras(), []);
  const [watch, setWatch] = useState<Watch | null>(card.watch);
  const [excluded, setExcluded] = useState<string[]>(card.watch?.excluded_cameras ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const others = (cameras.data ?? []).filter((c) => c.id !== cameraId);
  const home = (cameras.data ?? []).find((c) => c.id === cameraId);

  // "Turn on plate reading for this camera." is the camera's own setting (Edit on its page); it can be done here too
  async function turnOn(reader: string) {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.setReaders(cameraId, [...new Set([...(home?.readers ?? []), reader])]);
      cameras.setData((cameras.data ?? []).map((c) => (c.id === cameraId ? updated : c)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  // a clause the AI wrote as a signal's name (dwell_s) says nothing to a person; its tier still shows in the others
  const covered = card.coverage.filter((c) => c.tier !== "X" && !/^[a-z]+(_[a-z]+)+$/.test(c.clause.trim()));
  const bt = card.backtest;

  const set = (patch: Partial<Watch>) => setWatch((w) => (w ? { ...w, ...patch } : w));

  async function save() {
    if (!watch) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await api.saveWatch(cameraId, {
        text: card.text, watch: { ...watch, excluded_cameras: watch.scope === "site" ? excluded : [] },
        coverage: card.coverage, setup: card.setup, cannot: card.cannot, scope: watch.scope,
        excluded_cameras: watch.scope === "site" ? excluded : [],
      });
      if (saved.saved) onSaved();
      else setError(saved.problem ?? "That watch could not be saved. Change the words and check again.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="card-title" data-testid="watch-card">
      <h2 id="card-title" className="card-title" style={{ marginBottom: 12 }}>
        {watch ? `What ${brand.name} will watch` : `${brand.name} can't watch this`}
      </h2>
      <Facts>
        {covered.length > 0 && (
          <Fact term="Will watch">
            <ul>
              {covered.map((c) => (
                <li key={c.clause}>
                  <span className="clause">{plain(c.clause)}</span>
                  <span className="clause-tier">{TIER_WORDS[c.tier] ?? ""}{c.caveat ? `. ${plain(c.caveat)}` : ""}</span>
                </li>
              ))}
            </ul>
          </Fact>
        )}
        {card.cannot.length > 0 && (
          <Fact term="Can't watch" className="cannot">
            <ul>
              {card.cannot.map((c) => (
                <li key={c.clause}>
                  <p>{plain(c.reason)}</p>
                  {c.alternative && (
                    <>
                      <p className="small" style={{ marginTop: 6 }}>Nearest instead: {plain(c.alternative).replace(/\.$/, "")}.</p>
                      <button type="button" className="btn btn-sm" style={{ marginTop: 8 }} onClick={() => onInstead(c.alternative)}>
                        Watch for this instead
                      </button>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </Fact>
        )}
        {!watch && card.cannot.length === 0 && card.problem && <Fact term="Problem">{plain(card.problem)}</Fact>}
        {watch && card.setup.length > 0 && (
          <Fact term="Before it starts">
            <ul>
              {card.setup.map((s) => {
                const task = parseTask(s);
                if (task.kind !== "reader") return <li key={s}>{plain(s)}</li>;
                const on = (home?.readers ?? []).includes(task.reader);
                return (
                  <li key={s} className="task-row">
                    <span className={on ? "task-done" : undefined}>{plain(s)}{on && <span className="sr-only"> Done.</span>}</span>
                    {!on && (
                      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => turnOn(task.reader)}>
                        Turn on {task.label.toLowerCase()}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </Fact>
        )}
        {watch && (
          <Fact term="When it sees it">
            <Choices legend="When it sees it" hideLegend value={watch.output}
              options={[{ value: "alert", label: "Alert" }, { value: "log", label: "Write to the log" }]}
              onChange={(output) => set(output === "log" ? { output, severity: "info" } : { output, severity: watch.severity === "info" ? "warning" : watch.severity })} />
            {watch.output === "log" && <p className="hint" style={{ marginTop: 8 }}>A row in the log each time, and no alert.</p>}
          </Fact>
        )}
        {watch && watch.output === "alert" && (
          <Fact term="Level">
            <Choices legend="Level" hideLegend value={watch.severity} options={SEVERITIES} onChange={(severity) => set({ severity })} />
            <p className="hint" style={{ marginTop: 8 }}>{SEVERITY_HELP[watch.severity]}</p>
          </Fact>
        )}
        {watch && (
          <Fact term="Where">
            <Choices legend="Where" hideLegend value={watch.scope}
              options={[{ value: "camera", label: "This camera" }, { value: "site", label: "Every camera" }]}
              onChange={(scope) => set({ scope })} />
            {watch.scope === "site" && others.length > 0 && (
              <fieldset className="stack" style={{ border: 0, padding: 0, margin: "12px 0 0", gap: 8 }}>
                <legend className="hint" style={{ marginBottom: 8 }}>Leave out</legend>
                {others.map((c) => (
                  <label key={c.id} className="check">
                    <input type="checkbox" checked={excluded.includes(c.id)}
                      onChange={(e) => setExcluded(e.target.checked ? [...excluded, c.id] : excluded.filter((x) => x !== c.id))} />
                    <span>{c.name}</span>
                  </label>
                ))}
              </fieldset>
            )}
          </Fact>
        )}
        {watch && bt && (
          <Fact term="Last 24 hours">
            <p>{plain(bt.summary)}</p>
            {watch.scope !== (card.watch?.scope ?? "camera") && (
              <p className="hint" style={{ marginTop: 4 }}>Counted for {card.watch?.scope === "site" ? "every camera" : "this camera"}.</p>
            )}
          </Fact>
        )}
      </Facts>
      <Problem>{error}</Problem>
      {watch && (
        <div className="card-actions" style={{ marginTop: 8 }}>
          <button type="button" className="btn btn-primary" onClick={save} disabled={busy}>{busy ? "Saving" : "Save watch"}</button>
          <span className="hint">It starts at once on {watch.scope === "site" ? "every camera" : "this camera"}.</span>
        </div>
      )}
    </section>
  );
}

export default function WatchEditor() {
  const [params] = useSearchParams();
  const cameraId = params.get("camera") ?? "";
  const navigate = useNavigate();
  const { purposes } = useSession();
  const admin = useCan("admin");
  const camera = useLoad(() => api.camera(cameraId), [cameraId]);
  const [text, setText] = useState("");
  const [card, setCard] = useState<(WatchCard & { text: string }) | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const cam = camera.data;
  useTitle("Add a watch");

  useEffect(() => {
    box.current?.focus();
  }, []);

  async function check(words = text) {
    const t = words.trim();
    if (!t || busy) return;
    setText(t);
    setBusy(true);
    setError(null);
    setCard(null);
    try {
      setCard({ ...(await api.previewWatch(cameraId, { text: t })), text: t });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!cameraId || (camera.error && !cam)) {
    return (
      <Page narrow>
        <PageHead title="Add a watch" back={{ to: "/app", label: "Control room" }} />
        <Problem>{camera.error ?? "Open a camera first, then add a watch to it."}</Problem>
      </Page>
    );
  }

  const ideas = purposes?.[cam?.purpose ?? "other"]?.watches ?? [];
  const back = { to: `/app/cameras/${cameraId}`, label: cam?.name ?? "Camera" };

  if (!admin) {
    return (
      <Page narrow>
        <PageHead title="Add a watch" back={back} />
        <p className="notice">Only a society admin can add watches. Ask your admin to add this one.</p>
      </Page>
    );
  }

  return (
    <Page narrow>
      <PageHead title="Add a watch" back={back} sub={cam && <span>On {cam.name}</span>} />
      <form className="ask-big" onSubmit={(e) => { e.preventDefault(); check(); }}>
        <label htmlFor="watch-words">What should {brand.name} watch for?</label>
        <textarea id="watch-words" ref={box} className="textarea" rows={2} value={text} maxLength={300}
          onChange={(e) => setText(e.target.value)} placeholder={ideas[0] ? `For example: ${ideas[0]}` : "For example: someone near the wall after 10 PM"}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); check(); } }} />
        <div className="row">
          <button className="btn btn-primary" disabled={busy || !text.trim()}>{busy ? "Reading your words" : "Check it"}</button>
          {busy && <span className="working small"><span className="spinner" aria-hidden="true" />This takes a few seconds.</span>}
        </div>
      </form>

      {ideas.length > 0 && !card && !busy && (
        <section className="section" aria-labelledby="ideas-title">
          <h2 id="ideas-title" className="h3">Ideas for a {purposeLabel(purposes, cam?.purpose).toLowerCase()} camera</h2>
          <div className="chips">
            {ideas.map((idea) => (
              <button key={idea} type="button" className="chip" onClick={() => check(idea)}>{sentence(idea)}</button>
            ))}
          </div>
        </section>
      )}

      <Problem>{error}</Problem>
      {card && (
        <TheCard key={card.text} card={card} cameraId={cameraId}
          onInstead={(t) => { setText(t); check(t); }}
          onSaved={() => navigate(`/app/cameras/${cameraId}`, { state: { notice: `Watch saved: ${card.text}.` } })} />
      )}
      {card && <p className="small"><Link to={`/app/cameras/${cameraId}`}>Back to {cam?.name ?? "the camera"} without saving</Link></p>}
    </Page>
  );
}
