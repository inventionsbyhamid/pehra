import { Link } from "react-router-dom";
import { Mark } from "../components/Mark";
import { useTitle } from "../hooks";

export default function Terms({ standalone }: { standalone?: boolean }) {
  useTitle("Terms and privacy");
  const body = (
    <article className="page page-narrow terms">
      <div className="page-head">
        <div>
          <h1>Terms and privacy</h1>
          <p className="lede">Watchful is a demo built for the OpenCV AI Competition 2026. This page says plainly what it records, who it recognises, and what it will not do.</p>
        </div>
      </div>

      <section>
        <h2>What is recorded</h2>
        <p>For each camera, Watchful keeps a log of events: when something came into or left a zone, posture changes such as a fall, and camera problems such as being covered or moved. Each event has a time and, where it helps, a small picture of the moment and a summary of clothing colours. To make the footage searchable, Watchful also keeps small pictures of moments it saw, each with a numeric description of what is in it. None of this is face data.</p>
        <p>When a watch is triggered, Watchful saves a short clip from a few seconds before to a few seconds after the moment, and the steps the AI took to check it. Live video is not recorded in full.</p>
        <p>Only the moments OpenCV flags are sent to the AI model for checking, along with a few frames around them. Questions you ask are sent with the event log they need.</p>
      </section>

      <section>
        <h2>Who is recognised</h2>
        <p>Faces are recognised only for people enrolled under People, and only after you confirm they have agreed. Everyone else stays unknown. Watchful never stores face data for people who are not enrolled, and never matches faces against any outside database.</p>
        <p>Searching with a photo of a person needs the same consent. The photo is used for that search only and is not kept, and a photo of a stranger can't start a lookout.</p>
        <p>"Watch for this person again" uses clothing, not faces, and ends on its own after 24 hours.</p>
      </section>

      <section>
        <h2>Removing someone</h2>
        <p>You can remove an enrolled person at any time under People. Their face data is deleted and Watchful stops recognising them on every camera.</p>
      </section>

      <section>
        <h2>How long things are kept</h2>
        <p>Clips, pictures and events are deleted after 7 days. Videos uploaded by judges and visitors are deleted after 7 days too.</p>
      </section>

      <section>
        <h2>Possible intruder alerts</h2>
        <p>These judge behaviour only: lingering at a door, trying more than one way in, keeping hands at a door, climbing, or coming back again and again, inside the hours you choose. Clothing, skin, age and gender are never signals. Each alert says "possible" and leaves the decision to a person.</p>
      </section>

      <section>
        <h2>Falls</h2>
        <p>Fall detection is an aid, not a medical or safety guarantee. It can miss a fall, especially when someone is partly hidden, in low light or far from the camera, and it can mistake lying down for a fall. Do not rely on it as the only way to keep someone safe.</p>
      </section>

      <section>
        <h2>Nothing goes out without you</h2>
        <p>Serious alerts wait for a person to approve them before anyone is contacted. Your decision is saved with the alert.</p>
      </section>

      <section>
        <h2>Demo footage</h2>
        <p>The demo cameras replay clips recorded with the consent of the people in them, or openly licensed clips. There is no live feed from anyone's home.</p>
      </section>
    </article>
  );

  if (!standalone) return body;
  return (
    <div className="standalone">
      <header className="standalone-head">
        <Link to="/" className="brand"><Mark /><span className="brand-name">Watchful</span></Link>
        <Link to="/" className="small">Sign in</Link>
      </header>
      <main id="main" className="main">{body}</main>
    </div>
  );
}
