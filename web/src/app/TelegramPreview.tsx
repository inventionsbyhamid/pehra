import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type TelegramMessage } from "../api";
import brand from "../site/brand";
import { useCan } from "./session";
import "./demo.css";

// The message the society's Telegram group gets for this incident: the server makes it with the same code that
// posts it (alerts/telegram.py), so the words, the still with faces blurred and the buttons are exactly the group's.
// `version` changes when the incident does (acknowledged, seen again), and the message is asked for again, as
// Telegram's own message is edited.
export function TelegramPreview({ incidentId, version, demo }: { incidentId: string; version: string; demo?: boolean }) {
  const [msg, setMsg] = useState<TelegramMessage | null>(null);
  const [failed, setFailed] = useState(false);
  const admin = useCan("admin");

  useEffect(() => {
    let gone = false;
    api.telegramPreview(incidentId)
      .then((m) => !gone && (setMsg(m), setFailed(false)))
      .catch(() => !gone && setFailed(true));
    return () => {
      gone = true;
    };
  }, [incidentId, version]);

  if (failed && !msg) return <p className="small muted">The Telegram message could not be shown just now.</p>;
  if (!msg) return null;

  const [head, ...rest] = msg.caption.split("\n");
  let note: string;
  if (!msg.goes_to_telegram) note = "Warnings go to Telegram only when the society turns them on. This is what it would say.";
  else if (demo || msg.demo) note = "In the demo nothing is sent. This is the message your group would get, kept up to date as the incident changes.";
  else if (!msg.connected) note = "No Telegram group is connected yet, so nothing was sent. This is what it would say.";
  else note = "Edited in the group as the incident changes.";

  return (
    <div className="tg">
      <figure className="tg-message" aria-label="The Telegram message">
        {msg.photo ? (
          <img className="tg-photo" src={msg.photo} alt="The still Telegram gets, with faces of anyone not enrolled blurred" />
        ) : (
          <p className="tg-nophoto small">No picture: Telegram gets one only when faces in it could be checked and blurred.</p>
        )}
        <figcaption className="tg-text">
          <span className="tg-from">{brand.name}</span>
          <strong>{head}</strong>
          {rest.map((line, i) => <span key={i}>{line}</span>)}
        </figcaption>
        {msg.buttons.length > 0 && (
          <ul className="tg-buttons" aria-label="Its buttons">
            {msg.buttons.map((row, i) => (
              <li key={i}>
                {row.map((b) => <span key={b.text} className="tg-button">{b.text}</span>)}
              </li>
            ))}
          </ul>
        )}
      </figure>
      <p className="small muted">
        {note}
        {!demo && !msg.demo && !msg.connected && msg.goes_to_telegram && admin && <> <Link to="/app/settings/telegram">Connect a group</Link></>}
      </p>
    </div>
  );
}

export default TelegramPreview;
