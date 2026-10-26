// Drawings of the product's main pieces in HTML, with sample data for a made up society. They show what the app
// does; they are not screenshots.
import type { ComponentType } from "react";
import { GateScene, LobbyScene, ParkingScene, PoolScene, PoolStill, StaffGateScene } from "./art";
import brand from "./brand";

type TileState = "alert" | "live" | "offline";

const TILES: [string, ComponentType | null, TileState][] = [
  ["Pool", PoolScene, "alert"],
  ["Main gate", GateScene, "live"],
  ["Service gate", StaffGateScene, "live"],
  ["Parking", ParkingScene, "live"],
  ["Lobby", LobbyScene, "live"],
  ["Basement", null, "offline"],
];

const STATE_WORDS: Record<TileState, string> = { alert: "Alert", live: "Live", offline: "Offline" };

export function Severity({ level }: { level: "critical" | "warning" | "info" }) {
  return <span className={`sev sev-${level}`}>{level[0].toUpperCase() + level.slice(1)}</span>;
}

export function ControlRoom() {
  return (
    <figure id="control-room" className="room" aria-label={`A drawing of the ${brand.name} control room`}>
      <div className="room-bar">
        <span>Control room</span>
        <span>10:42 PM</span>
      </div>
      <ul className="room-wall" aria-label="Cameras">
        {TILES.map(([name, View, state]) => (
          <li key={name} className={`cam cam-${state}`}>
            <div className="cam-view">{View ? <View /> : <p className="cam-off">No picture since 9:58 PM</p>}</div>
            <p className="cam-name">
              <span>{name}</span>
              <span className="cam-state">
                <span className="dot" aria-hidden="true" />
                {STATE_WORDS[state]}
              </span>
            </p>
          </li>
        ))}
      </ul>
      <ul className="room-feed" aria-label="Alerts">
        <li className="feed-item">
          <Severity level="critical" />
          <p>
            <span className="feed-what">Someone in the pool after hours</span>
            <span className="feed-where">Pool, 10:42 PM</span>
          </p>
        </li>
        <li className="feed-item">
          <Severity level="warning" />
          <p>
            <span className="feed-what">Camera offline</span>
            <span className="feed-where">Basement, 9:58 PM</span>
          </p>
        </li>
      </ul>
    </figure>
  );
}

export function TelegramAlert() {
  return (
    <figure id="telegram-alert" className="tg" aria-label="A drawing of an alert in a society's Telegram group">
      <div className="tg-head">
        <span className="tg-avatar" aria-hidden="true">
          GH
        </span>
        <p>
          <span className="tg-group">Greenwood Heights security</span>
          <span className="tg-members">Telegram group, 6 members</span>
        </p>
      </div>
      <div className="tg-chat">
        <div className="tg-msg">
          <p className="tg-from">{brand.name}</p>
          <div className="tg-photo">
            <PoolStill />
          </div>
          <div className="tg-text">
            <p className="tg-title">Critical: someone in the pool after hours</p>
            <p>Pool camera, 10:42 PM</p>
            <p>One person in the water and nobody at the lifeguard chair since 10:30 PM.</p>
            <p className="tg-small">Faces blurred. The clip opens after sign in.</p>
          </div>
          <p className="tg-time">edited 10:43 PM</p>
        </div>
        <div className="tg-keys">
          <span>Acknowledge</span>
          <span>False alarm</span>
          <span className="tg-wide">Open the clip</span>
        </div>
        <p className="tg-service">Ravi acknowledged at 10:43 PM</p>
      </div>
    </figure>
  );
}

export function WatchCard() {
  return (
    <figure id="watch-card" className="mock watch" aria-label="A drawing of a watch written in plain words">
      <p className="watch-ask">What should {brand.name} watch for?</p>
      <p className="watch-typed">Tell me when a child is alone at the pool</p>
      <dl className="watch-facts">
        <div className="watch-cannot">
          <dt>Cannot watch</dt>
          <dd>Whether someone is a child. {brand.name} never guesses anyone's age.</dd>
        </div>
        <div>
          <dt>Will watch instead</dt>
          <dd>Anyone alone in the pool zone for 20 seconds</dd>
        </div>
        <div>
          <dt>Camera</dt>
          <dd>Pool</dd>
        </div>
        <div>
          <dt>Setup</dt>
          <dd>Draw the pool zone once</dd>
        </div>
        <div>
          <dt>Last 24 hours</dt>
          <dd>Would have alerted 2 times</dd>
        </div>
        <div>
          <dt>Level</dt>
          <dd>
            <Severity level="critical" />
          </dd>
        </div>
      </dl>
      <p className="watch-save">
        <span className="mock-btn">Save watch</span>
      </p>
    </figure>
  );
}

const PASSAGES: [string, string, string, "white" | "yellow" | null, string][] = [
  ["7:58 AM", "Car", "MH 12 KD 4471", "white", "In"],
  ["8:06 AM", "Cab", "MH 12 TC 2093", "yellow", "In"],
  ["8:21 AM", "Two wheeler", "Not read", null, "Out"],
  ["9:15 AM", "Cab", "MH 12 TC 2093", "yellow", "Out after 69 min"],
];

export function GateLog() {
  return (
    <figure id="gate-log" className="mock log" aria-label="A drawing of the gate log">
      <table>
        <caption>Gate log, Main gate, this morning</caption>
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Vehicle</th>
            <th scope="col">Plate</th>
            <th scope="col">Way</th>
          </tr>
        </thead>
        <tbody>
          {PASSAGES.map(([time, kind, plate, colour, way]) => (
            <tr key={time}>
              <td className="num">{time}</td>
              <td>{kind}</td>
              <td>
                {colour ? (
                  <span className={`plate plate-${colour}`}>
                    {plate}
                    <span className="visually-hidden">, {colour} plate</span>
                  </span>
                ) : (
                  <span className="faint">{plate}</span>
                )}
              </td>
              <td>{way}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="log-note">Yellow plates mark cabs and other commercial vehicles.</p>
    </figure>
  );
}

const STAFF: [string, string, string, string][] = [
  ["Sunita", "Cook", "7:12 AM", "11:40 AM"],
  ["Ramesh", "Driver", "8:05 AM", "Still in"],
  ["Lakshmi", "Housekeeping", "9:30 AM", "1:15 PM"],
];

export function StaffRegister() {
  return (
    <figure id="staff-register" className="mock log" aria-label="A drawing of the staff register">
      <table>
        <caption>Staff register, Service gate, today</caption>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Work</th>
            <th scope="col">First seen</th>
            <th scope="col">Last seen</th>
          </tr>
        </thead>
        <tbody>
          {STAFF.map(([name, work, first, last]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{work}</td>
              <td className="num">{first}</td>
              <td className="num">{last}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="log-note">Only adult staff who agreed, enrolled from a few photos.</p>
    </figure>
  );
}
