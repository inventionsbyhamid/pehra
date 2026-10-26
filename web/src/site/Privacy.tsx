import { Link } from "react-router-dom";
import brand, { mailto } from "./brand";
import { aiRoute, dataHome, lockWords, onAws, storageSecurity } from "./hosting";
import LegalPage from "./LegalPage";
import { merchant, plans, retentionText } from "./plans";

const name = brand.name;

export default function Privacy() {
  const email = <a href={mailto()}>{brand.contact_email}</a>;
  return (
    <LegalPage
      title="Privacy notice"
      intro={
        <p>
          This notice explains what {name} stores from your society's cameras, where it is kept, for how long, and who
          else handles it. Questions or requests go to {email}.
        </p>
      }
    >
      <h2>Who decides</h2>
      <p>
        <strong>Data from your cameras</strong>, such as footage of residents, visitors and staff, vehicle plates and the
        faces of enrolled staff, belongs to your society. Under India's Digital Personal Data Protection Act, 2023 and the
        DPDP Rules, 2025, the society (its association or committee) is the data fiduciary and {name} is its data
        processor. We handle that data only to run {name} for the society and on its instructions, as set out in the{" "}
        <Link to="/terms">terms</Link>.
      </p>
      <p>
        <strong>Your account data</strong>, such as the name and email of the people who sign in, is ours to look after:
        we decide how it is used, as described here.
      </p>

      <h2>What we store</h2>
      <ul>
        <li>Account: names, emails and Google account ids, the society's name and time zone, and each member's role.</li>
        <li>
          Cameras: names, purposes, stream addresses (which can include a camera's password), and the zones and lines
          drawn on them.
        </li>
        <li>
          Footage: stills and short clips from watched cameras, and a record of what was seen in them, such as people,
          vehicles and times, which the watches and their backtests use.
        </li>
        <li>
          Logs: vehicles at the gates (time, direction, kind of vehicle, plate text and colour when read, a picture) and
          staff attendance (first seen and last seen each day).
        </li>
        <li>
          Staff enrolment: for each enrolled adult staff member, their name, their work, a record of their consent, and
          face templates, which are numbers made from 3 to 5 photos. The photos are not kept after enrolment.
        </li>
        <li>Alerts: what happened, the reason given, and who acknowledged it or marked it a false alarm, and when.</li>
        <li>Telegram: the id of the group your society connects.</li>
        <li>Billing: your plan and subscription status from {merchant}. We never see card or bank details.</li>
        <li>Security logs: sign ins, clip views, cameras added, enrolments and exports.</li>
        {onAws && <li>Access logs from our web server, which hold IP addresses.</li>}
      </ul>

      <h2>Faces</h2>
      <p>
        {name} names only adult staff, such as maids, drivers, cooks and guards, who agreed to be enrolled, with their
        consent recorded. It never enrols, identifies or estimates the age of children, and it has no watches about
        children. Residents and visitors are never identified: to {name} they are a person, a car or a cab.
      </p>
      <p>
        Staff can say no. Not enrolling is the option without a face, and the society should keep another way to log
        their work. When a worker leaves or withdraws consent, the society deletes their enrolment and their face
        templates are deleted with it.
      </p>

      <h2>Telegram and clips</h2>
      <p>
        Alerts sent to Telegram carry a still picture, the reason, the camera and the time. Faces of people who are not
        enrolled staff are blurred in that picture. Blurring is automatic, so a face can occasionally be missed. Video
        is never sent to Telegram: clips stay behind sign in, open only to the society's team by role, and every view of
        a clip is recorded. There is no bulk download of footage.
      </p>
      <p>
        Your society chooses who is in its Telegram group. Messages there are handled by Telegram under its own policy.
      </p>

      <h2>Notices at each camera zone</h2>
      <p>
        The society puts up a notice at each camera zone saying what the camera is for, how long footage is kept and
        whom to contact with a grievance. {name} makes a printable notice for each zone.
      </p>

      <h2>How long we keep it</h2>
      <ul>
        {plans.map((p) => (
          <li key={p.id}>
            {p.name}: {retentionText(p).toLowerCase()}.
          </li>
        ))}
      </ul>
      <p>
        When that period ends, footage, clips, stills, gate log rows and attendance rows are deleted automatically.
        Incident clips are kept for the same period. {lockWords}
      </p>
      <p>
        To close your society's account, email {email} from the owner's account email. We then delete its data,
        except records the law requires us to keep, such as invoices.
        {onAws ? " Access logs, which hold IP addresses, are kept for a year." : ""}
      </p>

      <h2>AI checks</h2>
      <p>
        OpenCV on our servers does the watching. When it flags a moment that needs a closer look, {name} can send a few
        stills of that moment and the watch's words to an AI vision model {aiRoute}.
        {onAws ? " Amazon Bedrock does not use this data to train models." : ""} The AI never sees the live stream or
        whole clips, and a daily limit caps how many checks each society makes.
      </p>

      <h2>Where it is kept</h2>
      <p>
        {dataHome} {storageSecurity} Storage is private, and pictures and clips open through short lived links.
      </p>

      <h2>Who else handles data</h2>
      <ul>
        <li>Amazon Web Services: hosting, storage and Amazon Bedrock{onAws ? "" : `, once ${name} runs there`}.</li>
        {!onAws && <li>The company that hosts our test server: hosting and storage while {name} runs there.</li>}
        {!onAws && <li>OpenRouter: AI checks from the test server.</li>}
        <li>Telegram: alerts to the group your society connects.</li>
        <li>{merchant}: checkout, subscriptions, tax and invoices, as merchant of record.</li>
        <li>Google: sign in.</li>
        <li>Cloudflare: our domain and the network that carries traffic to {name}.</li>
      </ul>
      <p>We do not sell data and we do not use advertising or tracking cookies. One cookie keeps you signed in.</p>

      <h2>Your rights and grievances</h2>
      <p>
        If you are a resident, visitor or staff member and want to see, correct or erase data about you in your
        society's footage or logs, ask your society's committee first, as it decides; we help it answer. For your own
        account data, email {email} from your account email.
      </p>
      <p>
        Our grievance contact is {brand.operator}, at {email}. If you are not satisfied with the answer, you can complain
        to the Data Protection Board of India.
      </p>

      <h2>Breaches</h2>
      <p>If a breach affects personal data, we tell the societies affected within 72 hours of finding out.</p>
    </LegalPage>
  );
}
