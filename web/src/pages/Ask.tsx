import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Thumb } from "../components/Thumb";
import { Trace } from "../components/Trace";
import { dayLabel, plain, span } from "../format";
import { useLoad, useTitle } from "../hooks";
import type { Answer } from "../types";

type Turn = { id: number; question: string; scope: string; answer?: Answer; error?: string };

function suggestions(zones: string[]): string[] {
  const out = zones.slice(0, 2).map((z) => `When did people come into ${z}?`);
  return [...out, "Who was seen in the last hour?", "Did anyone fall today?"];
}

const CONFIDENCE: Record<Answer["confidence"], { label: string; cls: string }> = {
  high: { label: "Sure", cls: "tag-good" },
  medium: { label: "Fairly sure", cls: "tag-accent" },
  low: { label: "Not sure", cls: "tag-warning" },
};

function AnswerView({ answer }: { answer: Answer }) {
  const c = CONFIDENCE[answer.confidence] ?? CONFIDENCE.medium;
  return (
    <div className="answer stack">
      <div className="row" style={{ gap: 8 }}>
        <span className={`tag ${c.cls}`}>{c.label}</span>
      </div>
      <p className="answer-text">{plain(answer.text)}</p>
      {answer.items.length > 0 && (
        <ol className="list answer-items" data-testid="answer-items">
          {answer.items.map((it, i) => (
            <li key={`${it.event_id}-${i}`} className="answer-item">
              <Thumb src={it.thumbnail_url} alt={plain(it.label)} w={120} h={90} />
              <div className="stack" style={{ gap: 2 }}>
                <p className="answer-label">{plain(it.label)}</p>
                {it.start_ts != null && (
                  <p className="small num">
                    {dayLabel(it.start_ts) !== "Today" && `${dayLabel(it.start_ts)} `}{span(it.start_ts, it.end_ts)}
                  </p>
                )}
                {it.clip_url && <a className="small" href={it.clip_url}>Play the clip</a>}
              </div>
            </li>
          ))}
        </ol>
      )}
      {answer.trace.length > 0 && (
        <details className="answer-trace">
          <summary className="small">How I found this ({answer.trace.length} {answer.trace.length === 1 ? "step" : "steps"})</summary>
          <Trace steps={answer.trace} />
        </details>
      )}
    </div>
  );
}

export default function Ask() {
  useTitle("Ask");
  const cameras = useLoad(() => api.cameras(), []);
  const [scope, setScope] = useState("");
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const names = Object.fromEntries((cameras.data ?? []).map((c) => [c.id, c.name]));

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
  }, [turns]);

  async function ask(q: string) {
    const text = q.trim();
    if (!text || busy) return;
    const turn: Turn = { id: Date.now(), question: text, scope };
    setTurns((t) => [...t, turn]);
    setQuestion("");
    setBusy(true);
    try {
      const answer = await api.ask(text, scope || undefined);
      setTurns((t) => t.map((x) => (x.id === turn.id ? { ...x, answer } : x)));
    } catch (e) {
      setTurns((t) => t.map((x) => (x.id === turn.id ? { ...x, error: (e as Error).message } : x)));
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  }

  return (
    <div className="page page-narrow ask-page">
      <div className="page-head">
        <div>
          <h1>Ask about the footage</h1>
          <p className="lede">Ask in your own words. Answers come from what OpenCV recorded, with times and pictures, and say so when they are not sure.</p>
        </div>
      </div>

      <div className="conversation" aria-live="polite">
        {turns.length === 0 && (
          <div className="suggest stack">
            <p className="small muted">Try one of these</p>
            <div className="row">
              {suggestions([...new Set((cameras.data ?? []).flatMap((c) => c.zones.map((z) => z.name)))]).map((s) => <button key={s} className="btn btn-sm" onClick={() => ask(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {turns.map((t) => (
          <div key={t.id} className="turn">
            <div className="q">
              <p>{t.question}</p>
              <p className="xsmall muted">{t.scope ? names[t.scope] ?? "One camera" : "All cameras"}</p>
            </div>
            {t.answer ? <AnswerView answer={t.answer} /> : t.error ? (
              <p className="notice notice-error" role="alert">{t.error}</p>
            ) : (
              <div className="row small muted" role="status"><span className="spinner" aria-hidden="true" />Reading the event log and looking at pictures</div>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>

      <form className="ask-box" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
        <label className="sr-only" htmlFor="ask-input">Your question</label>
        <textarea id="ask-input" ref={input} className="textarea" rows={2} value={question} onChange={(e) => setQuestion(e.target.value)}
          placeholder="When did the maid come into the kitchen and when did she leave?"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(question); } }} />
        <div className="spread">
          <select className="select select-sm" aria-label="Which cameras to ask about" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="">All cameras</option>
            {(cameras.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button className="btn btn-primary" disabled={busy || !question.trim()}>{busy ? "Looking" : "Ask"}</button>
        </div>
      </form>
    </div>
  );
}
