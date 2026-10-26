import { Link } from "react-router-dom";
import agentWorkflow from "@docs/agent-workflow.svg";
import architecture from "@docs/architecture.svg";
import overview from "@docs/overview.svg";
import { DEMO_URL } from "./actions";
import brand, { mailto } from "./brand";
import { Code, DOCS, DocsPage, MEASURED, REPO, repoFile } from "./Docs";
import { cameraPlan, freePlan, plans, retentionText } from "./plans";

const name = brand.name;

function Figure(props: { src: string; width: number; height: number; alt: string; caption: string }) {
  return (
    <figure className="docs-figure">
      <a href={props.src}>
        <img src={props.src} width={props.width} height={props.height} alt={props.alt} />
      </a>
      <figcaption>{props.caption} Open the picture for full size.</figcaption>
    </figure>
  );
}

function Overview() {
  return (
    <Figure
      src={overview}
      width={1040}
      height={560}
      alt={`${name} overview: cameras feed ingest and OpenCV perception; watches in plain words and an agent with OpenCV tools turn flagged moments into incidents for the control room and Telegram; logs feed the gate log and the staff register; it runs on one box on AWS or any Linux machine.`}
      caption="Every piece at a glance."
    />
  );
}

function Architecture() {
  return (
    <Figure
      src={architecture}
      width={1120}
      height={748}
      alt={`${name} architecture: the committee in a browser, a phone or laptop, the ${name} bridge and CCTV recorders over RTSP reach one EC2 Graviton4 instance with COOL in AWS Mumbai. On it the web app, the camera processes running OpenCV 5, the AI check with its OpenCV tools and the job worker; flagged moments go to the AI check, incidents to the job worker, and alerts to the society's Telegram group. S3, Bedrock, Parameter Store, CloudWatch, SNS and CloudFormation complete the stack.`}
      caption="The parts and where they run."
    />
  );
}

function AgentWorkflow() {
  return (
    <Figure
      src={agentWorkflow}
      width={1040}
      height={772}
      alt={`${name} agent workflow: OpenCV perceives each frame, the watch engine flags a moment or sends nothing, camera health and staff faces skip the AI, and the AI check calls OpenCV tools (more frames, zoom, brighten, denoise, flow, pose, path walked) in a loop until it alerts with a grade, asks a person, cannot judge, or dismisses. An incident goes to the control room and Telegram, and Acknowledge or False alarm comes back.`}
      caption="Perceive, decide, act: the AI check and its OpenCV tools."
    />
  );
}

const PAGE_LINES: Record<string, string> = {
  "/docs/getting-started": "Sign in, add cameras, write a watch and connect Telegram.",
  "/docs/how-it-works": "What OpenCV does on each frame, and what the AI may and may not decide.",
  "/docs/privacy-and-safety": "Children, faces, blur, what the AI sees and how long footage is kept.",
  "/docs/self-hosting": `Run ${name} on your own Linux machine with Docker.`,
  "/docs/aws": "The AWS services it uses, and how to deploy the stack.",
};

export function DocsHome() {
  return (
    <DocsPage
      path="/docs"
      title="Documentation"
      lead={
        <p>
          {name} is an AI control room for the CCTV cameras a housing society already has. OpenCV 5 watches every
          camera; anyone can add a watch in plain words; an agent checks each flagged moment with OpenCV tools; and the
          society's Telegram group gets a graded alert with faces blurred.
        </p>
      }
    >
      <ul className="docs-cards">
        {DOCS.filter((d) => d.path !== "/docs").map((d) => (
          <li key={d.path}>
            <Link to={d.path}>{d.label}</Link>
            <p>{PAGE_LINES[d.path]}</p>
          </li>
        ))}
      </ul>
      <h2>The whole system</h2>
      <Overview />
      <h2>Elsewhere</h2>
      <ul>
        <li>
          <a href={DEMO_URL}>Try the demo</a>: a private sandbox society for a day, with no sign in.
        </li>
        <li>
          The code, the technical report and the evaluation: <a href={REPO}>{REPO.replace("https://", "")}</a>
        </li>
        <li>
          Questions: <a href={mailto()}>{brand.contact_email}</a>
        </li>
      </ul>
    </DocsPage>
  );
}

export function GettingStarted() {
  return (
    <DocsPage
      path="/docs/getting-started"
      title="Getting started"
      lead={<p>From signing in to the first alert in your security group on Telegram.</p>}
    >
      <h2>1. Sign in, or try the demo</h2>
      <p>
        <a href={DEMO_URL}>Try the demo</a> opens a private sandbox society for a day, with the Plaza demo camera
        (OpenCV's sample clip), an example incident and the Telegram message it would send. You can add this laptop or
        a phone as a camera and type a watch of your own.
      </p>
      <p>
        Signing in with Google creates your society on the {freePlan.name} plan, with you as its owner. A self hosted
        server can also print a single use sign in link (see <Link to="/docs/self-hosting">Self hosting</Link>).
      </p>

      <h2>2. Add your cameras</h2>
      <p>In the control room, choose Add a camera, then how it connects:</p>
      <dl className="docs-terms">
        <dt>A recorder</dt>
        <dd>A Hikvision, CP Plus or Dahua NVR or DVR the server can reach. Pick the brand and the camera number.</dd>
        <dt>A camera address</dt>
        <dd>The RTSP address of an IP camera (rtsp or rtsps only).</dd>
        <dt>Through the bridge</dt>
        <dd>
          For a recorder on the society's own network: a small OpenCV program on a computer there reads it and sends
          pictures out. The app gives the line for its cameras file.
        </dd>
        <dt>A phone, or this device</dt>
        <dd>
          A spare phone (scan the QR code, press Start) or this laptop becomes a camera while its page is open. Faces
          are blurred on the device before any picture leaves it.
        </dd>
        <dt>Upload a recording</dt>
        <dd>A video file, scanned once on the server, so you can ask what happened in it.</dd>
      </dl>
      <p>
        Then say what the camera watches: pool, main gate, service gate, parking, lobby, perimeter or other. Each
        purpose suggests ready watches, such as "someone in the water after pool hours" or "log every vehicle in and
        out". Keep them, or remove them.
      </p>

      <h2>3. Draw zones and lines</h2>
      <p>
        On the camera's page, draw the areas your watches talk about, such as the pool water, the lifeguard chair or a
        fire lane, and a line across a gate with its inward direction. The gate log and "came in" or "went out" come
        only from crossing a line with a direction.
      </p>

      <h2>4. Write a watch in plain words</h2>
      <p>
        Type what to look for. Before you save, a card shows what it will watch and how reliably, what it cannot and
        the nearest thing it can, any setup it needs (such as a zone to draw), and how often it would have fired in the
        last 24 hours of this camera. Then choose what happens:
      </p>
      <ul>
        <li>Critical: sent to your Telegram group at once, and again if nobody acknowledges it.</li>
        <li>Warning: on the dashboard, and on Telegram if your society turns that on.</li>
        <li>Info, or write to the log: kept in the watch log and on the camera's history.</li>
      </ul>
      <p>
        Some watches are refused on purpose. Nothing about anyone's age or about children is ever saved. A watch about a
        person (an unknown person, a known one or a staff member) works only on entry, gate and service door cameras.
      </p>

      <h2>5. Connect Telegram</h2>
      <ol>
        <li>An admin opens Settings, Telegram, Connect a group.</li>
        <li>Add the server's bot to your security group on Telegram.</li>
        <li>Send the message shown there, /connect and a code, in that group. The code works once, for 30 minutes.</li>
      </ol>
      <p>
        Critical alerts then arrive in the group with a still picture, the reason and Acknowledge and False alarm
        buttons. Anyone in the group can press them, and the app shows who did. On your own server, the operator first
        gives it a bot (see <Link to="/docs/self-hosting">Self hosting</Link>).
      </p>

      <h2>6. Invite your team</h2>
      <p>Settings, Team invites people by role. Each role may do what the ones before it may:</p>
      <ul>
        <li>Viewer: sees the cameras and incidents, and asks what happened.</li>
        <li>Supervisor: also acknowledges incidents, marks false alarms, and sees the logs and enrolled staff.</li>
        <li>Admin: also adds cameras and watches, enrols staff, connects Telegram and invites people.</li>
        <li>Owner: also changes roles and removes people.</li>
      </ul>

      <h2>7. Gate log and staff register</h2>
      <p>
        With the {cameraPlan.name} plan and above, Logs holds a gate log of vehicles in and out with the time, a
        picture, and the plate text and colour when they can be read, paired by plate; and a staff register of
        consenting adult staff, enrolled from 3 to 5 photos that are not kept. The register shows when each person was
        first seen and last seen each day; "came in" and "went out" are written only when they cross a line drawn with
        a direction. The watch log is on every plan.
      </p>
    </DocsPage>
  );
}

const OPENCV: [string, string, string][] = [
  ["Read cameras and recordings", "cv2.VideoCapture with FFmpeg", "RTSP streams, uploads and the demo clip"],
  ["Pictures from phones and the bridge", "cv2.imdecode after a size check", "a tiny file cannot decode to gigabytes"],
  ["Skip still scenes", "createBackgroundSubtractorMOG2, morphologyEx, findContours", "the detector runs when something moves"],
  ["Dark frames", "createCLAHE", "brightened before detection at night"],
  ["People and vehicles", "YOLOX nano on cv2.dnn, NMSBoxesBatched", "every analysed frame"],
  ["Tracking", "cv2.KalmanFilter", "steady ids for dwell time and line crossings"],
  ["Zones and lines", "pointPolygonTest", "inside, entering and crossing with a direction"],
  ["Falls and posture", "MediaPipe pose on cv2.dnn, warpAffine", "torso angle over time"],
  ["Movement inside a box", "DISOpticalFlow_create", "a still body in moving water"],
  ["Faces", "FaceDetectorYN (YuNet), FaceRecognizerSF (SFace)", "blur everyone; match consenting staff at entries"],
  ["Plates", "LPD-YuNet and PP-OCR on cv2.dnn, warpPerspective", "the gate log's plate text and colour"],
  ["Camera health", "ORB, BFMatcher, findHomography with USAC_MAGSAC, Laplacian", "turned, blurred or covered cameras"],
  ["Gate open or closed", "GaussianBlur, CLAHE, Sobel, matchTemplate", "a zone's state against example pictures"],
  ["Looks described in words", "SigLIP 2 on ONNX Runtime, or cv2.dnn", "the image and text model; OpenCV prepares every crop"],
  ["The agent's tools", "resize and unsharp mask, CLAHE, fastNlMeansDenoisingColored, DIS flow", "a closer look at a flagged moment"],
  ["Telegram stills", "GaussianBlur on face boxes, imencode", "faces of anyone not enrolled are blurred"],
  ["The phone camera page", "OpenCV.js FaceDetectorYN", "faces blurred before a picture leaves the phone"],
];

export function HowItWorks() {
  return (
    <DocsPage
      path="/docs/how-it-works"
      title="How it works"
      lead={
        <p>
          OpenCV does the watching on every camera. The AI only checks the moments OpenCV flags, and never watches a
          stream.
        </p>
      }
    >
      <Overview />
      <h2>From a camera to an alert</h2>
      <ol>
        <li>Pictures arrive from recorders and IP cameras over RTSP, the bridge, a phone or laptop, or an upload.</li>
        <li>
          Camera processes run the OpenCV pipeline at the analysis rate: detect, track, zones and lines, pose, faces at
          entries, plates, camera health.
        </li>
        <li>
          A watch in plain words is compiled once, by the AI, into general signals such as "a person inside the pool
          zone after 8 PM". A rule engine checks those signals on every analysed frame and flags a candidate.
        </li>
        <li>The agent checks the candidate with OpenCV tools and decides what happens.</li>
        <li>An incident opens, graded critical, warning or info, with a still and a clip, in the control room and on Telegram.</li>
        <li>Gate crossings and staff seen at entries are written to the logs.</li>
      </ol>
      <Architecture />

      <h2>What OpenCV does</h2>
      <div className="docs-table" role="region" aria-label="OpenCV in each step" tabIndex={0}>
        <table>
          <thead>
            <tr>
              <th scope="col">Step</th>
              <th scope="col">OpenCV</th>
              <th scope="col">Why</th>
            </tr>
          </thead>
          <tbody>
            {OPENCV.map(([step, cv, why]) => (
              <tr key={step}>
                <th scope="row">{step}</th>
                <td>
                  <code>{cv}</code>
                </td>
                <td>{why}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>The agent</h2>
      <AgentWorkflow />
      <p>
        The agent is an LLM that sees only a flagged moment: a few stills and the watch's words. It can look closer
        with OpenCV tools (frames from the moment, a sharpened zoom, brighten, denoise or sharpen, optical flow inside
        the box, the person's pose over time, the path walked), then it must finish one way:
      </p>
      <ul>
        <li>raise the alert, with a grade and a short reason;</li>
        <li>dismiss it, because the pictures show it is not what the watch asks for;</li>
        <li>ask a person, for a serious moment someone should decide;</li>
        <li>or say it cannot judge, when the question needs someone's age, which it never judges.</li>
      </ul>
      <p>What the AI does not decide:</p>
      <ul>
        <li>A critical alert goes to Telegram at once, before the check. A critical watch is dismissed only when the pictures clearly show everyone is safe.</li>
        <li>Past the daily AI limit, or when the AI cannot be reached, a moment is still filed, marked not checked by the AI.</li>
        <li>Camera health and enrolled staff faces are measured by OpenCV; no AI looks at them.</li>
        <li>It never names anyone. Only OpenCV recognises people, and only consenting adult staff at entries.</li>
      </ul>
      <p>
        On AWS the AI is Claude Haiku 5.5 on Amazon Bedrock. The live demo uses an OpenRouter model. The same AI
        also compiles watches, answers questions about what happened, and searches footage by words.
      </p>
      <p>
        The full design, the evaluation and the failure cases are in the{" "}
        <a href={repoFile("docs/report.md")}>technical report</a>.
      </p>
    </DocsPage>
  );
}

export function PrivacySafety() {
  return (
    <DocsPage
      path="/docs/privacy-and-safety"
      title="Privacy and safety"
      lead={
        <p>
          What {name} will never do, what it keeps and for how long. The legal notice is on the{" "}
          <Link to="/privacy">privacy page</Link>.
        </p>
      }
    >
      <h2>Children</h2>
      <p>
        {name} never enrols, identifies or estimates the age of children, and it has no watches about children. A watch
        that asks about age is refused before it is saved, in words, numbers, heights and many Indian languages and
        scripts. The AI is told never to judge age, and its text is checked again before anyone sees it.
      </p>

      <h2>Faces</h2>
      <ul>
        <li>Only adult staff who agreed are enrolled, from 3 to 5 photos. The photos are not kept, only templates.</li>
        <li>
          Staff are matched only on entry, gate and service door cameras. The register keeps when each was first seen
          and last seen each day.
        </li>
        <li>Residents and visitors are never identified: to {name} they are a person, a car or a cab.</li>
        <li>Nobody can search footage with a photo of a face.</li>
        <li>A lookout that follows a person by how they look is off unless the operator turns it on.</li>
      </ul>

      <h2>Blur</h2>
      <p>
        Telegram gets still pictures, never video, with the faces of anyone not enrolled blurred. A phone or laptop used
        as a camera blurs faces with OpenCV.js before a picture leaves it. Blurring is automatic, so a face can
        occasionally be missed. Clips stay behind sign in, open by role, and every view is recorded.
      </p>

      <h2>What the AI sees</h2>
      <p>
        A few stills of a flagged moment and the watch's words, never the live stream or whole clips. Each society has
        a daily limit, and so does the whole server. The live demo asks OpenRouter for providers that keep nothing.
      </p>

      <h2>How long footage is kept</h2>
      <ul>
        {plans.map((p) => (
          <li key={p.id}>
            {p.name}: {retentionText(p).toLowerCase()}.
          </li>
        ))}
      </ul>
      <p>
        After that, footage, clips, stills, gate log rows and register rows are deleted automatically. Example pictures
        a society saves for a zone's state are kept, and choosing them well is the society's responsibility.
      </p>

      <h2>Who decides</h2>
      <p>
        Under India's DPDP Act the society is the data fiduciary and {name} its processor. {name} makes a printable
        notice for each camera zone. To close a society's account, email{" "}
        <a href={mailto()}>{brand.contact_email}</a> from the owner's account email; there is no self serve deletion yet.
      </p>
    </DocsPage>
  );
}

const STEPS_ENV = `
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"   # once for SESSION_SECRET, once for DATA_KEY`;

export function SelfHosting() {
  return (
    <DocsPage
      path="/docs/self-hosting"
      title="Self hosting"
      lead={
        <p>
          {name} runs on any 64 bit Linux machine with Docker, in the society or in a cloud. One container holds the
          app, the camera processes and the job worker; everything it keeps is in one data volume.
        </p>
      }
    >
      <h2>What you need</h2>
      <ul>
        <li>Docker with Compose, on x86 or ARM.</li>
        <li>
          Disk: the image is {MEASURED.imageSize} with every model ({MEASURED.modelsSize}), plus footage for your
          retention days.
        </li>
        <li>
          Memory and CPU: on our dev box, a 2013 Xeon without AVX2, {MEASURED.benchCameras} busy cameras on{" "}
          {MEASURED.benchCpus} CPUs used {MEASURED.benchMemory} in all and analysed {MEASURED.benchFps} per camera (
          <a href={repoFile("eval/results/bench-devbox_6cpu_no_text.json")}>the measurement</a>). A watch described by
          looks costs much more on an old CPU like this one.
        </li>
        <li>
          An https address for phone cameras and Telegram: a Cloudflare tunnel, or your own proxy. On plain http only
          localhost can use a camera in the browser.
        </li>
      </ul>

      <h2>1. Get the code and write .env</h2>
      <Code label="Get the code">{`
git clone ${REPO}.git
cd pehra`}</Code>
      <Code label="Write the settings">{STEPS_ENV}</Code>
      <p>
        Then set, in <code>.env</code>:
      </p>
      <dl className="docs-terms">
        <dt><code>SESSION_SECRET</code>, <code>DATA_KEY</code></dt>
        <dd>
          Two random strings from the command above. Keep DATA_KEY: camera addresses sealed with a lost key cannot be
          read.
        </dd>
        <dt><code>PUBLIC_BASE_URL</code></dt>
        <dd>The https address people open, used in links and Telegram messages.</dd>
        <dt><code>OPENROUTER_API_KEY</code></dt>
        <dd>
          The AI, for writing watches, checking flagged moments and answering questions. Or set AI_PROVIDER=bedrock
          with BEDROCK_MODEL and AWS credentials. Without either, a critical moment still alerts, marked unverified.
        </dd>
        <dt><code>TELEGRAM_BOT_TOKEN</code>, <code>TELEGRAM_WEBHOOK_SECRET</code></dt>
        <dd>The token BotFather gives your bot, and one more random string. Telegram needs the https address.</dd>
        <dt><code>GOOGLE_CLIENT_ID</code>, <code>GOOGLE_CLIENT_SECRET</code></dt>
        <dd>
          Optional Google sign in, with https://your.address/auth/google/callback as the redirect. Without them, sign in
          is by single use links you print on the server.
        </dd>
        <dt><code>TRUST_CLOUDFLARE</code>, <code>ALLOW_PRIVATE_STREAMS</code></dt>
        <dd>
          true only behind a Cloudflare tunnel; true only when the machine is inside the society's network and pulls
          cameras on private addresses.
        </dd>
      </dl>
      <p>
        Every other setting is explained in{" "}
        <a href={repoFile(".env.example")}>
          <code>.env.example</code>
        </a>
        .
      </p>

      <h2>2. Start it</h2>
      <Code label="Build and start">{`
docker compose up -d --build
docker compose ps`}</Code>
      <p>
        The first build downloads every model and checks each by SHA 256. The app listens on port 8000 of this machine
        only, and is healthy once <code>/healthz</code> answers. The database is migrated on every start.
      </p>

      <h2>3. Sign in for the first time</h2>
      <Code label="Print a sign in link">{`
docker compose exec pehra python -m watchful.auth signin-link you@example.com --workspace "Green Park"`}</Code>
      <p>
        Open the printed link: it creates the society and makes you its owner. The Python package is called watchful,
        after the project {name} grew out of.
      </p>

      <h2>4. Add a camera and connect Telegram</h2>
      <p>
        Add cameras as in <Link to="/docs/getting-started">Getting started</Link>. For Telegram, make a bot with
        BotFather, put its token and a webhook secret in .env, and run <code>docker compose up -d</code> again; the app
        tells Telegram where to send updates. Then Settings, Telegram, Connect a group, and send the /connect code in
        your group.
      </p>

      <h2>5. Https with a Cloudflare tunnel (optional)</h2>
      <p>
        In the Cloudflare dashboard, make a tunnel with a public hostname pointing at http://pehra:8000, save its token
        as tunnel.token next to compose.yaml, readable only by the tunnel's user, and uncomment the tunnel service in
        compose.yaml. Set PUBLIC_BASE_URL to that hostname and TRUST_CLOUDFLARE=true.
      </p>
      <Code label="Protect the tunnel token">{`
sudo chown 65532 tunnel.token && sudo chmod 400 tunnel.token
docker compose up -d`}</Code>

      <h2>6. Backups and upgrades</h2>
      <p>
        Everything is in the <code>pehra-data</code> volume: the database, pictures and clips. To copy it:
      </p>
      <Code label="Back up the data volume">{`
docker compose stop pehra
docker compose run --rm --no-deps --user root -v "$PWD:/backup" pehra tar czf /backup/pehra-data.tgz -C /app/data .
docker compose start pehra`}</Code>
      <p>To upgrade, back up first, then:</p>
      <Code label="Upgrade">{`
git pull
docker compose up -d --build`}</Code>

      <h2>Without Docker Compose</h2>
      <p>
        The development path builds <code>deploy/Dockerfile.dev</code>, runs <code>scripts/fetch_assets.py</code> and
        uvicorn by hand. It is in the <a href={`${REPO}#self-hosting`}>README</a>.
      </p>
    </DocsPage>
  );
}

const AWS_SERVICES: [string, string][] = [
  ["EC2 on Graviton4", "One c8g instance runs everything with OpenCV 5 on Arm. Given the COOL image id, the same template runs COOL's OpenCV instead."],
  ["Amazon S3", "Pictures, clips and a Litestream copy of the database."],
  ["Amazon Bedrock", "One model checks flagged moments, compiles watches and answers questions: Amazon Nova 2 Lite on our deploy, Claude Haiku 5.5 with Marketplace access."],
  ["SSM Parameter Store", "Settings and secrets, loaded when each service starts."],
  ["CloudWatch", "Logs, metrics, a dashboard and alarms."],
  ["SNS", "Alarm emails to the operator."],
  ["CloudFormation and Budgets", "The whole stack from one template, with a monthly budget alarm."],
];

export function AwsDocs() {
  return (
    <DocsPage
      path="/docs/aws"
      title="AWS"
      lead={
        <p>
          {name} runs on AWS at <a href="https://aws.pehra.dijmo.com">aws.pehra.dijmo.com</a>, in Mumbai on one
          Graviton instance, from one CloudFormation template.
        </p>
      }
    >
      <Architecture />
      <h2>What each service does</h2>
      <dl className="docs-terms">
        {AWS_SERVICES.map(([service, job]) => (
          <div key={service}>
            <dt>{service}</dt>
            <dd>{job}</dd>
          </div>
        ))}
      </dl>
      <p>
        Running the instance is estimated at about {MEASURED.awsMonth} a month, an estimate from list prices and not a
        bill, before Bedrock, S3 and data transfer (
        <a href={repoFile("eval/results/build_measurements.json")}>the estimate</a>).
      </p>

      <h2>Deploy it</h2>
      <ol>
        <li>An AWS account on the Paid plan, and the AWS command line with a profile named pehra.</li>
        <li>In AWS Marketplace, subscribe to COOL for Graviton4 and copy its image id for Mumbai.</li>
        <li>In the Bedrock console, turn on access to Claude Haiku 5.5.</li>
        <li>Point Google sign in, the Polar webhook and your Telegram bot at your domain.</li>
        <li>Copy the parameters file, keep it private, and fill in the COOL image id, an alert email and your keys.</li>
        <li>Run the deploy as a dry run, read it, then run it.</li>
        <li>Point the domain's A record at the address it prints, and open /healthz.</li>
      </ol>
      <Code label="Deploy commands">{`
cp deploy/params.env.example deploy/params.env && chmod 600 deploy/params.env
deploy/deploy.sh --dry-run
deploy/deploy.sh`}</Code>
      <p>
        The deploy refuses to finish unless OpenCV from COOL is the one running and the app fits its memory budget.
        Details are in the{" "}
        <a href={`${REPO}#aws`}>README</a> and <a href={repoFile("deploy/cloudformation.yaml")}>the template</a>.
      </p>
    </DocsPage>
  );
}
