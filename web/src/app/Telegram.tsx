import { useState } from "react";
import { api } from "../api";
import { at, epoch } from "../format";
import { useLoad } from "../hooks";
import { atLeast, useSession } from "./session";
import { Copy } from "./ui/Copy";
import { Problem } from "./ui/Page";

const minutes = (m: number) => (m === 1 ? "1 minute" : Number.isInteger(m) ? `${m} minutes` : `${Math.round(m * 60)} seconds`);

export function Telegram() {
  const { me } = useSession();
  const admin = atLeast(me.role, "admin");
  const status = useLoad(() => api.telegram(), [], 10_000);
  const [code, setCode] = useState<{ command: string; expires_at: string; bot_username: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const s = status.data;
  const bot = code?.bot_username ?? s?.bot_username;
  const reminders = s?.reminders;

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await status.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Telegram</h1>
      <p className="lede" style={{ marginTop: -16 }}>
        Critical alerts go to your security group on Telegram at once, with a picture, the camera and two buttons:
        Acknowledge and False alarm.
      </p>
      <section className="card stack" aria-labelledby="group-rules-title">
        <h2 id="group-rules-title" className="h2">How the group works</h2>
        <ul className="list rules">
          <li>Anyone in a connected group can press Acknowledge or False alarm, even if they are not in this app. The app shows who pressed it.</li>
          {reminders && reminders.times > 0 && (
            <li>
              {reminders.times === 1
                ? `A critical alert nobody acknowledges is posted in the group once more, ${minutes(reminders.every_min)} later.`
                : `A critical alert nobody acknowledges is posted in the group again ${minutes(reminders.every_min)} later, and every ${minutes(reminders.every_min)} after that, up to ${reminders.times} times.`}
            </li>
          )}
          <li>Pictures sent to the group have the faces of people who are not enrolled staff blurred. Clips stay in the app.</li>
        </ul>
      </section>
      <Problem>{status.error ?? error}</Problem>
      {s && !s.ready && (
        <p className="notice">{s.message ?? "Telegram is not connected."} Once the operator gives this server a Telegram bot, you can connect your security group here.</p>
      )}

      {s?.ready && (
        <section className="card stack" aria-labelledby="groups-title">
          <h2 id="groups-title" className="h2">Connected groups</h2>
          {s.groups.length === 0 ? (
            <p className="small muted">None yet. Connect your security group below.</p>
          ) : (
            <ul className="list people-rows">
              {s.groups.map((g) => (
                <li key={g.id}>
                  <span className="grow">
                    <strong>{g.title ?? "A Telegram group"}</strong>
                    {g.connected_at && <span className="small muted"> since {at(epoch(g.connected_at))}</span>}
                  </span>
                  {admin && (
                    <button className="btn btn-sm btn-quiet btn-danger" disabled={busy} onClick={() => run(() => api.telegramRemove(g.id))}>
                      Disconnect<span className="sr-only"> {g.title}</span>
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {s?.ready && admin && (
        <section className="card stack" aria-labelledby="connect-title">
          <h2 id="connect-title" className="h2">Connect a group</h2>
          <ol className="stack" style={{ paddingLeft: "1.2rem", margin: 0, gap: 8 }}>
            <li>Add {bot ? <strong>@{bot}</strong> : "the bot"} to your security group on Telegram.</li>
            <li>Send the message below in that group. The code works once, for 30 minutes.</li>
          </ol>
          {code ? (
            <div className="stack" style={{ gap: 6 }}>
              <Copy value={code.command} label="Message to send in the group" />
              <p className="hint">Works until {at(epoch(code.expires_at))}. This page shows the group once it is connected.</p>
            </div>
          ) : (
            <div>
              <button className="btn btn-primary" disabled={busy}
                onClick={() => run(async () => setCode(await api.telegramConnect()))}>Make a code</button>
            </div>
          )}
        </section>
      )}

      {s?.ready && (
        <section className="card" aria-labelledby="warn-title">
          <div className="spread" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
            <div>
              <h2 id="warn-title" className="h2">Warnings on Telegram too</h2>
              <p className="small muted" style={{ marginTop: 4 }}>
                Critical alerts always go. Warnings show here in the app, and on Telegram too when this is on. Info stays in the logs and the 9 AM summary.
              </p>
            </div>
            <label className="switch">
              <input type="checkbox" checked={s.warnings} disabled={!admin || busy} aria-labelledby="warn-title"
                onChange={(e) => run(() => api.telegramWarnings(e.target.checked))} />
              <span />
            </label>
          </div>
        </section>
      )}
    </>
  );
}
