import type { Watch } from "../types";
import { plain } from "../format";
import { watchSentence } from "../watchWords";

export function WatchSentence({ watch, people, small }: { watch: Watch; people?: Record<string, string>; small?: boolean }) {
  const parts = watchSentence(watch, people);
  return (
    <div className="stack" style={{ gap: 2 }}>
      <p className={`sentence${small ? " sentence-sm" : ""}`}>
        {parts.map((p, i) =>
          p.slot ? (
            <span key={i} className="slot">
              <b>{p.text}</b>
              <i>{p.slot}</i>
            </span>
          ) : (
            <span key={i} className="w">{p.text}</span>
          ),
        )}
      </p>
      {watch.verify && (
        <p className="verify-line">
          Before it tells you, the AI looks again and asks <q>{plain(watch.verify)}</q>
        </p>
      )}
    </div>
  );
}
