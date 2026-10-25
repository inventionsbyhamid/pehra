import { Link } from "react-router-dom";
import brand, { mailto } from "./brand";
import { dataHome, lockWords, storageSecurity } from "./hosting";
import LegalPage from "./LegalPage";
import { merchant } from "./plans";

const name = brand.name;

export default function Terms() {
  const email = <a href={mailto()}>{brand.contact_email}</a>;
  return (
    <LegalPage
      title="Terms of service"
      intro={
        <p>
          These terms cover your society's use of {name}. They are short on purpose. If anything is unclear, email{" "}
          {email}.
        </p>
      }
    >
      <h2>Who we are</h2>
      <p>
        {name} is run by {brand.operator} in {brand.country}. In these terms "we" means {name}, and "you" means the
        society, association, committee or facility company that signs up, together with the people it lets use its
        account.
      </p>

      <h2>What {name} does</h2>
      <p>
        {name} watches the cameras you connect, using OpenCV on our servers. It raises alerts for the watches you set,
        sends critical alerts to the Telegram group you connect, keeps a gate log of vehicles and a register of enrolled
        staff, and tells you when a camera stops working.
      </p>

      <h2>An aid, not a guarantee</h2>
      <p>
        {name} helps people notice what matters. It can miss things and it can raise false alarms. It does not detect
        drowning, and it does not replace guards, lifeguards, your own recorder or emergency services. Alerts say
        possible; decisions stay with people. It is not a guarantee of anyone's safety.
      </p>

      <h2>Your account</h2>
      <p>
        You sign in with Google. Give each person the role they need, keep device links private, and remove people who
        no longer work with the society. You are responsible for what happens in your account.
      </p>

      <h2>Acceptable use</h2>
      <ul>
        <li>
          Connect only cameras your society runs, covering common areas such as gates, the pool, parking and lobbies.
          Never cameras inside homes, toilets or changing rooms.
        </li>
        <li>Put up a notice at each camera zone. {name} makes a printable one for each zone.</li>
        <li>
          Enrol only adult staff who give consent, keep that consent on record, offer a way to work there without a
          face, and delete an enrolment when the person leaves or withdraws consent.
        </li>
        <li>Never enrol or try to identify children.</li>
        <li>
          Do not use {name} to track or profile residents or visitors, for example by religion, caste, region or who
          visits whom.
        </li>
        <li>Share clips only with the people who need them, such as the committee or the police.</li>
        <li>Do not try to break, overload, copy or get around the limits of the service.</li>
      </ul>
      <p>We may suspend an account that breaks these rules. Where we can, we will tell you first and explain why.</p>

      <h2>Your data and our role</h2>
      <p>
        Footage, logs and staff enrolments hold personal data about residents, visitors and staff. For that data your
        society is the data fiduciary and {name} is its data processor under India's Digital Personal Data Protection
        Act, 2023. Most duties under the DPDP Rules, 2025 apply from 13 May 2027. This section is our
        processing agreement with you.
      </p>
      <ul>
        <li>We process that data only to provide {name} to you and on your instructions, and we never sell it.</li>
        <li>
          Where we keep it: {dataHome} {storageSecurity}
        </li>
        <li>
          The service providers we use, and what each one does, are listed in the{" "}
          <Link to="/privacy">privacy notice</Link>. We tell you before adding a new one.
        </li>
        <li>If a breach affects your data, we tell you within 72 hours of finding out and help you meet your duties.</li>
        <li>
          We help you answer requests and grievances from people in your footage, such as a request to see or erase
          their data. Your notices name your own grievance contact; ours is {brand.operator}, at {email}.
        </li>
        <li>
          We delete the data when your plan's retention period ends or when you close your account. {lockWords}
        </li>
      </ul>

      <h2>Plans, payment and cancelling</h2>
      <p>
        Paid plans are priced per watched camera and billed in rupees, monthly or yearly, and they renew until you
        cancel. Prices exclude GST. {merchant} is our merchant of record: it takes the payment, adds tax where it
        applies and issues your invoice.
      </p>
      <p>
        You can cancel any time from Billing in the app. The plan keeps working until the end of the period you paid for,
        then your society moves to Free. Refunds follow the <Link to="/refunds">refund policy</Link>. If a renewal
        payment fails, the plan keeps working for a few days while you fix it, then moves to Free.
      </p>
      <p>If we change prices, we email you at least 30 days before the change, and it applies from your next renewal.</p>

      <h2>Availability</h2>
      <p>
        We work to keep {name} running, but it can be interrupted for maintenance or by problems outside our control,
        such as a camera's network or Telegram itself, and alerts can arrive late. Keep your own recorder running.
      </p>

      <h2>Limits of liability</h2>
      <p>
        {name} is provided as it is. As far as the law allows, we are not liable for indirect or consequential losses,
        or for events an alert did not catch. Our total liability to you for anything related to {name} is limited to
        the fees you paid us in the 12 months before the event that caused it.
      </p>

      <h2>Ending</h2>
      <p>
        You can close your society's account at any time. To close your account, email {email} from the owner's account
        email. We then delete your data, except records the law requires us to keep, such as invoices.
      </p>

      <h2>Changes and law</h2>
      <p>
        We may update these terms and will tell you by email or in the app before important changes take effect. These
        terms are governed by the laws of India.
      </p>
    </LegalPage>
  );
}
