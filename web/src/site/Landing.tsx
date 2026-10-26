import { Link } from "react-router-dom";
import { StartActions, TryDemo } from "./actions";
import { Plus } from "./art";
import brand, { mailto } from "./brand";
import { dataHome } from "./hosting";
import PlanGrid, { PlanLinks, PriceExample } from "./PlanGrid";
import { cameraPlan, freePlan, largePlan } from "./plans";
import { ControlRoom, GateLog, Severity, StaffRegister, TelegramAlert, WatchCard } from "./product";
import SiteLayout from "./SiteLayout";

const name = brand.name;
const freeCameras = freePlan.limits.watched_cameras;
const freeDays = freePlan.limits.retention_days;
const paidDays = cameraPlan.limits.retention_days;

type Source = [string, string];

// Every number here comes from research/society-market-2026-10-25.md, with the source it names.
const PAINS: { title: string; text: string; sources: Source[] }[] = [
  {
    title: "Nobody watches live",
    text: "In most small societies nobody watches the CCTV live. At best a busy guard glances at one monitor, and footage is looked at only after something goes wrong. People watching nine screens miss about 60% of what happens on them, against 20% on four.",
    sources: [
      ["Studio Matrx", "https://www.studiomatrx.org/guides/cctv-control-room-monitoring-india"],
      ["Ambient, citing Sulman 2008", "https://info.ambient.ai/hubfs/White%20Papers/White-Paper-TheHumanCeiling.pdf"],
    ],
  },
  {
    title: "Gate apps log what guards type",
    text: "Visitor apps record what the guard taps, not what the camera saw. MyGate's own help page says the final authority rests with the security guard, and one Noida society handles over 1,000 deliveries a day on 12 hour shifts.",
    sources: [
      [
        "MyGate help",
        "https://help.mygate.in/articles/134062-why-are-the-visitors-still-coming-to-my-flat-without-my-approval",
      ],
      [
        "ThePrint",
        "https://theprint.in/india/security-guards-vs-delivery-agents-its-a-new-source-of-tension-across-delhi-ncr-societies/3049348/",
      ],
    ],
  },
  {
    title: "Pools left unattended",
    text: "In Gurugram in July 2024 a 5 year old drowned in a society pool while the lifeguards were reportedly busy on their phones. In Bengaluru a 7 year old drowned after the guard was moved and the pool was left unattended.",
    sources: [
      ["The Tribune", "https://www.tribuneindia.com/news/delhi/5-year-old-boy-drowns-in-gurugram-society-pool-643276"],
      [
        "Deccan Herald",
        "https://www.deccanherald.com/india/karnataka/bengaluru/seven-year-old-girl-drowns-in-swimming-pool-in-bengaluru-apartment-3062765",
      ],
    ],
  },
  {
    title: "Dead cameras nobody notices",
    text: "At one Kharar society only 150 of 350 cameras worked. In Bhopal, CCTV had not worked for five years when a man fell into a lift shaft; he was found 10 days later.",
    sources: [
      ["The Tribune", "https://www.tribuneindia.com/news/chandigarh/residents-flag-rise-in-thefts-at-kharar-society/amp"],
      [
        "ETV Bharat",
        "https://www.etvbharat.com/en/state/man-falls-into-lift-shaft-in-bhopal-body-found-after-10-days-enn26012006311",
      ],
    ],
  },
];

const FEATURES = [
  ["One control room", "Every camera on one wall, and one feed of what needs attention. Nothing else on the screen."],
  [
    "Any watch in plain words",
    `Type what to look for. ${name} shows what it can watch, what it cannot, and how often it would have alerted yesterday.`,
  ],
  [
    "Graded alerts on Telegram",
    "Critical alerts reach your security group at once, with a picture and a reason. Warnings wait on the dashboard.",
  ],
  [
    "A gate log of every vehicle",
    "Cars, cabs and two wheelers in and out, with the time, a picture and the plate when it can be read.",
  ],
  [
    "A staff register from consented faces",
    "Maids, drivers and cooks who agree are enrolled from a few photos, and when they were first and last seen is noted each day.",
  ],
  [
    "Camera health",
    "A camera that goes offline, is covered, blurred or moved raises an alert instead of failing quietly.",
  ],
];

const LEVELS = [
  ["critical", "Sent to your Telegram group at once, and again if nobody acknowledges it."],
  ["warning", "Shown on the dashboard, and on Telegram if your society turns that on."],
  ["info", "Kept as a note in the watch log and on the camera's history, and counted in the 9 AM daily summary."],
] as const;

const STEPS = [
  [
    "Add your cameras",
    "Connect cameras over RTSP, which most IP cameras and recorders offer, or use a laptop or phone as a camera. Say what each one is for: pool, main gate, service gate, parking.",
  ],
  ["Keep or write watches", "Each purpose suggests ready watches. Keep them, or write your own in plain words."],
  [
    "Connect Telegram",
    "Add the bot to your security group and send it a one time code. Critical alerts start arriving there.",
  ],
];

const PROMISES = [
  ["Adult staff only, by consent", "Faces only for adult staff who agree, never children. Residents and visitors are never named."],
  [
    "Pictures, not video, on Telegram",
    "Telegram gets a still picture with faces blurred, unless the person is enrolled staff. Clips stay behind sign in.",
  ],
  ["Deleted on schedule", `Footage is deleted after your plan's days: ${freeDays} on ${freePlan.name}, ${paidDays} on ${cameraPlan.name}.`],
  ["The AI sees moments, not streams", "It sees only the few stills OpenCV has flagged, never the live stream."],
  ["Your society decides", `Under India's DPDP Act the society is the data fiduciary and ${name} its processor.`],
  [
    "A notice at every camera zone",
    "A printable notice for each zone, with its purpose, how long footage is kept and whom to contact.",
  ],
];

const FAQ = [
  [
    "Do we need new cameras?",
    `No. Most IP cameras and recorders, including Hikvision, CP Plus and Dahua, offer an RTSP stream that ${name} can read. A laptop or phone can join as a camera too. If your recorder can only be reached inside the society, email us and we will help you connect it.`,
  ],
  [
    "Does it replace our guards?",
    `No. ${name} helps your guards and supervisor notice what matters on cameras nobody can watch all day, and gives the committee a record of what happened.`,
  ],
  [
    "Who gets the alerts?",
    "The security group your society connects on Telegram, usually the supervisor, the guards and the committee. Not every resident.",
  ],
  [
    "Can it identify residents or visitors?",
    "No. It names only adult staff who agreed to be enrolled. Everyone else is a person, a car or a cab.",
  ],
  [
    "Where is the footage kept, and for how long?",
    `${dataHome} Footage is kept ${freeDays} days on ${freePlan.name} and ${paidDays} days on ${cameraPlan.name}, then deleted.`,
  ],
  [
    "What does the AI see?",
    "Only moments OpenCV has already flagged: a few stills, never the live stream. It checks them and writes the reason you read in the alert.",
  ],
];

function Sources({ list }: { list: Source[] }) {
  return (
    <p className="sources">
      Source{list.length > 1 ? "s" : ""}:{" "}
      {list.map(([label, url], i) => (
        <span key={url}>
          {i > 0 && ", "}
          <a href={url}>{label}</a>
        </span>
      ))}
    </p>
  );
}

export default function Landing() {
  return (
    <SiteLayout>
      <section className="hero" aria-labelledby="hero-title">
        <div className="wrap hero-grid">
          <div className="hero-copy">
            <h1 id="hero-title">{brand.tagline}</h1>
            <p className="hero-lead">{brand.description}</p>
            <div className="hero-actions">
              <StartActions />
            </div>
            <p className="hero-note">
              Free for {freeCameras} cameras, with the cameras you already have. The demo needs no sign in.{" "}
              <Link to="/pricing">See pricing</Link>
            </p>
          </div>
          <ControlRoom />
        </div>
      </section>

      <section className="band" id="pains" aria-labelledby="pains-title">
        <div className="wrap">
          <h2 id="pains-title">Dozens of cameras. Nobody watching.</h2>
          <p className="lead">
            Most societies record everything and look only after something goes wrong. Four problems come up again and
            again in the news and on the ground.
          </p>
          <ul className="pains">
            {PAINS.map((p) => (
              <li key={p.title}>
                <h3>{p.title}</h3>
                <p>{p.text}</p>
                <Sources list={p.sources} />
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="band band-tint" id="features" aria-labelledby="features-title">
        <div className="wrap">
          <h2 id="features-title">What {name} does</h2>
          <p className="lead">
            It watches the cameras you choose, all the time, with OpenCV on our servers, and speaks up only when
            something matters.
          </p>
          <ul className="features">
            {FEATURES.map(([title, text]) => (
              <li key={title}>
                <h3>{title}</h3>
                <p>{text}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="band" id="watches" aria-labelledby="watches-title">
        <div className="wrap show">
          <div className="show-copy">
            <h2 id="watches-title">Any watch, in plain words</h2>
            <p className="lead">
              Write it the way you would tell a guard. {name} turns it into a watch on one camera or on every camera,
              and says plainly what it cannot watch, with the nearest thing it can.
            </p>
            <p>
              Before a watch goes live it runs over the last day of footage, so you see how often it would have
              alerted. Ages are never guessed, so pool watches are about situations, not about who is in the water.
            </p>
          </div>
          <WatchCard />
        </div>
      </section>

      <section className="band band-tint" id="alerts" aria-labelledby="alerts-title">
        <div className="wrap show show-flip">
          <div className="show-copy">
            <h2 id="alerts-title">Alerts that say why</h2>
            <p className="lead">
              Each alert has a level and a plain reason. A critical one arrives as a single message that updates as
              things change, so a long situation is one message, not hundreds.
            </p>
            <ul className="levels">
              {LEVELS.map(([level, text]) => (
                <li key={level}>
                  <Severity level={level} />
                  <span>{text}</span>
                </li>
              ))}
            </ul>
            <p>
              Anyone in the group can acknowledge an alert or mark it a false alarm, and {name} counts those taps to
              show how often each watch is right.
            </p>
          </div>
          <TelegramAlert />
        </div>
      </section>

      <section className="band" id="logs" aria-labelledby="logs-title">
        <div className="wrap show">
          <div className="show-copy">
            <h2 id="logs-title">Logs that fill themselves</h2>
            <p className="lead">
              The gate log is written from what the camera saw, not from what someone remembered to type. It sits next
              to your visitor app and catches what it misses: a cab that stayed too long, a vehicle nobody logged.
            </p>
            <p>
              The staff register notes when each enrolled worker first comes in and last goes out. Plates are read on
              a best effort basis, and both logs export to CSV.
            </p>
          </div>
          <div className="show-stack">
            <GateLog />
            <StaffRegister />
          </div>
        </div>
      </section>

      <section className="band band-tint" id="how" aria-labelledby="how-title">
        <div className="wrap">
          <h2 id="how-title">How it starts</h2>
          <ol className="steps">
            {STEPS.map(([title, text]) => (
              <li key={title}>
                <h3>{title}</h3>
                <p>{text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="band" id="privacy" aria-labelledby="privacy-title">
        <div className="wrap split">
          <div>
            <h2 id="privacy-title">Privacy promises</h2>
            <p className="lead">
              Cameras in a society see families, children and staff every day. These rules are part of how {name}{" "}
              works, not settings someone can forget.
            </p>
            <p className="more-link">
              <Link to="/privacy" className="text-link">
                Read the privacy notice
              </Link>
            </p>
          </div>
          <ul className="promises">
            {PROMISES.map(([title, text]) => (
              <li key={title}>
                <h3>{title}</h3>
                <p>{text}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="band honest" id="limits" aria-labelledby="limits-title">
        <div className="wrap honest-box">
          <h2 id="limits-title">An aid, not a guarantee</h2>
          <p>
            {name} helps people notice what matters; it does not replace guards or lifeguards, and it does not detect
            drowning. At the pool it watches for situations: someone in the water after hours, the pool open with
            nobody at the lifeguard chair, a person still in the water too long. Alerts say possible, and people decide.
          </p>
        </div>
      </section>

      <section className="band band-tint" id="pricing" aria-labelledby="pricing-title">
        <div className="wrap">
          <h2 id="pricing-title">Pricing</h2>
          <p className="lead">Pay only for the cameras {name} watches. Prices exclude GST.</p>
          <PlanGrid />
          <PriceExample />
          <PlanLinks pricing />
        </div>
      </section>

      <section className="band" id="questions" aria-labelledby="questions-title">
        <div className="wrap split">
          <h2 id="questions-title">Questions</h2>
          <div className="faq">
            {FAQ.map(([q, a]) => (
              <details key={q}>
                <summary>
                  <span>{q}</span>
                  <Plus />
                </summary>
                <p className="faq-answer">{a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      <section className="closing" aria-labelledby="closing-title">
        <div className="wrap closing-grid">
          <div>
            <h2 id="closing-title">See it on sample cameras.</h2>
            <p>The demo opens a sample society with its cameras, alerts and logs, and needs no sign in.</p>
          </div>
          <div className="closing-actions">
            <div className="closing-buttons">
              <TryDemo className="cta cta-light" />
            </div>
            <p className="closing-small">
              Run a mall, school or township? Email{" "}
              <a href={mailto(`${name} for ${largePlan.name.toLowerCase()}`)}>{brand.contact_email}</a>.
            </p>
          </div>
        </div>
      </section>
    </SiteLayout>
  );
}
