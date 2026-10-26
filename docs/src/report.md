# Pehra technical report

An AI control room for the CCTV cameras Indian housing societies already have, built on OpenCV 5 and AWS.
OpenCV AI Competition 2026 entry by Hamid Mahmood Siddiqui (solo, India).

* Live demo: {{link.demo}} (press Try the demo)
* Video: {{link.video}}
* Code: {{link.repo}}
* Diagrams: [architecture](architecture.svg) and [agent workflow](agent-workflow.svg)

Every measured number in this report is filled in by `docs/fill.py` from a file in `eval/results/` (or the plans
file), and links to that file; hover a number to see its JSON path. A number that has not been measured yet says "not
measured yet". A setting or design figure, which nobody measures, is written plainly. Facts from outside the project
link to their sources, listed again at the end.

## What is live and what is waiting

| Part | State today |
| --- | --- |
| Public site, Try the demo, control room, camera pages, watch editor with its coverage card and backtest, incidents with the Telegram preview, gate log and staff register, add a camera (this device, a phone by QR, a recorder, the bridge, an address, an upload), settings, billing panel | Live on our test server at {{link.demo}}, a temporary Cloudflare tunnel address until the `pehra.dijmo.com` tunnel is set. The test server is one x86 container (an ioflood server, not AWS): stock OpenCV 5.0.0.93, not COOL; files on its own disk, not S3; the AI checks run on DeepSeek V4.1 Flash through OpenRouter, not Bedrock |
| Demo cameras | Only Plaza, OpenCV's sample clip `vtest.avi`. The Pool, Main gate and Service gate cameras appear by themselves when the owner's consented recordings are dropped into `data/samples/demo/`; until then a sandbox's gate log is empty, and its staff register lists six made up staff with seeded attendance |
| Telegram | Code done and tested against a mocked Bot API (connect code, webhook, posts, edits, reminders, taps). No bot token is set on the test server, so nothing is posted; the incident page shows exactly what the group would get |
| Google sign in | Code done and tested; off on the test server (no keys). Visitors use Try the demo; the operator can issue a sign in link |
| Billing per watched camera through Polar | Code done and tested against the SDK's own types; Polar has never been called (no sandbox products or keys yet) |
| AWS deployment (CloudFormation, EC2 c8g.xlarge from the COOL image, S3, Bedrock, CloudWatch, SSM, in Mumbai) | Template and one command deploy done and tested without AWS (`cfn-lint`, moto, a fake `aws`). The real deploy waits on the owner's go ahead |
| COOL benchmark | Built (`eval/bench.py`, `eval/bench_aws.sh`) and run only on our x86 dev box. The AWS run waits on the owner's go ahead; until it runs, section 6 reads "not measured yet" |
| Evaluation on society cameras | The harness runs the live engine on labelled clips. Today it has one clip, the Plaza sample, labelled by eye (section 8). No real gate, pool or society footage yet; the recording kit is `eval/record_checklist.md` |

## Where each judging line and deliverable is answered

| Judging line | Sections |
| --- | --- |
| Technical execution (30): OpenCV 5 depth, architecture, reliability, evaluation | [3](#3-architecture), [4](#4-opencv-5-implementation), [8](#8-evaluation) |
| Innovation (20) | [2](#2-what-pehra-does), [4.2](#42-watches-for-any-event), [7](#7-the-agent-agentic-vision) |
| Real world impact (20) | [1](#1-the-problem-and-who-has-it), [8](#8-evaluation), [11](#11-what-we-did-not-do-and-what-we-measured-on-what) |
| User experience (10): usability, accessibility | [9](#9-user-experience-and-accessibility) |
| Documentation and presentation (10) | this report, the diagrams, the [README](../README.md), the [video script](video-script.md) |
| Cloud delivery, reproducibility and responsible operation (10) | [5](#5-aws-deployment), [10](#10-responsible-use-security-and-privacy), [14](#14-reproduce) |
| Best Use of COOL award | [6](#6-cool-on-aws-graviton) |
| Agentic Vision award | [7](#7-the-agent-agentic-vision), the [agent workflow](agent-workflow.svg) |
| Limitations and failure cases | [8.6](#86-failure-cases), [11](#11-what-we-did-not-do-and-what-we-measured-on-what), [12](#12-limitations) |

| Required deliverable | Where |
| --- | --- |
| Technical report: problem, users, architecture, OpenCV 5, AWS, evaluation, limitations, responsible use | This file, sections 1 to 12 |
| Repository with pinned dependencies and build, deploy and test instructions | {{link.repo}}; the [README](../README.md); `requirements.lock`, `web/package-lock.json` |
| Architecture diagram with the OpenCV 5, AWS, COOL and agent parts | [architecture.svg](architecture.svg) and [agent-workflow.svg](agent-workflow.svg), with their Mermaid sources beside them |
| Working web endpoint | {{link.demo}} |
| Video of five minutes or less | {{link.video}}; the plan is in [video-script.md](video-script.md) |
| Evaluation evidence with failure cases | [Section 8](#8-evaluation), `eval/results/`, the failure pictures in `eval/results/failures/` |

## 1. The problem and who has it

An Indian housing society runs dozens of cameras, sometimes hundreds, and nobody watches them.

1. **Nobody watches.** In most small societies "nobody is watching the CCTV live": at best a busy guard glances at
   one monitor, and footage is reviewed after an incident
   ([Studio Matrx control room guide](https://www.studiomatrx.org/guides/cctv-control-room-monitoring-india)).
   Human attention fails fast: observers missed {{say:60%}} of targets across nine screens against {{say:20%}} across
   four, and performance drops after about twenty minutes
   ([Ambient white paper, citing Sulman 2008](https://info.ambient.ai/hubfs/White%20Papers/White-Paper-TheHumanCeiling.pdf)).
2. **Dead cameras nobody notices.** At Jalvayu Tower in Kharar about 150 of 350 cameras worked
   ([The Tribune](https://www.tribuneindia.com/news/chandigarh/residents-flag-rise-in-thefts-at-kharar-society/amp)).
   In Bhopal the CCTV had been "non functional for five years" when a man of 77 fell into a lift shaft and was found
   ten days later ([ETV Bharat, Jan 2026](https://www.etvbharat.com/en/state/man-falls-into-lift-shaft-in-bhopal-body-found-after-10-days-enn26012006311)).
3. **The gate.** One Noida society handles over 1,000 deliveries a day on guard shifts of twelve hours
   ([ThePrint, Sep 2026](https://theprint.in/india/security-guards-vs-delivery-agents-its-a-new-source-of-tension-across-delhi-ncr-societies/3049348/)).
   Gate apps log what guards type, not what cameras see; MyGate's own help page says "the final authority... rests
   with the security guard" ([MyGate help](https://help.mygate.in/articles/134062-why-are-the-visitors-still-coming-to-my-flat-without-my-approval)).
4. **Pools left unattended.** In Gurugram a boy of five drowned in a society pool while lifeguards were "busy on
   their phones", and the case named the society's management too
   ([The Tribune](https://www.tribuneindia.com/news/delhi/5-year-old-boy-drowns-in-gurugram-society-pool-643276)).
   In Bengaluru a girl of seven drowned in an apartment pool after the guard was moved elsewhere and the pool was
   left unattended ([Deccan Herald](https://www.deccanherald.com/india/karnataka/bengaluru/seven-year-old-girl-drowns-in-swimming-pool-in-bengaluru-apartment-3062765)).
5. **Staff attendance** is logged by guard taps in gate apps, not seen by cameras
   ([MyGate daily help](https://mygate.com/daily-help-management/)).

Guarding is expensive: Bengaluru agencies list {{say:₹16,000 to ₹28,000}} per guard a month
([IndiaMART listings: Kavitha](https://m.indiamart.com/kavithaenterpriseskarnataka/security-services.html),
[Royal Guardforce](https://m.indiamart.com/royalguardforcesecurityservice),
[Adecco](https://m.indiamart.com/adeccostaffingsolutionsindia/security-services.html)), and a post
watched all day and night needs four to five guards
([Studio Matrx guard guide](https://www.studiomatrx.org/guides/hiring-and-managing-society-guards-india)).

**Who uses Pehra.** The committee or facility manager (owner or admin: sets up cameras, watches, staff, Telegram and
billing), the security supervisor (reads the alert feed, acknowledges, marks false alarms, reads the logs) and the
guards in the society's Telegram group (they get the alerts and can tap Acknowledge or False alarm without an
account). Residents are not users in this version. Later buyers are malls and schools, with other suggested watches.

**Society feedback.** No society has used Pehra yet. The need above comes from news reports, guides and product help
pages, not from our own users.

## 2. What Pehra does

Pehra watches the cameras a society already has, live, with OpenCV, and speaks up only when something matters.

* **A calm control room.** A wall of camera tiles with a health dot each, and one alert feed. Nothing else on the
  screen.
* **Any watch in plain words.** "Someone in the water after pool hours", "gate left open after 11 PM", "log every
  cab", "someone carrying a ladder near the terrace door after 8 PM". Pehra compiles the words into OpenCV building
  blocks and shows a card before anything runs: what it will watch and how reliably, what it cannot watch and the
  nearest thing it can, the setup it needs ("Draw a line called main gate"), and how often it would have fired in
  the last 24 hours. A watch can cover one camera or every camera.
* **Graded alerts on Telegram.** Critical, warning or info. A critical alert goes to the society's Telegram group at
  once, before any AI check, as one message per incident that is edited as things change, with Acknowledge and False
  alarm buttons, and is posted again if nobody acknowledges. The still crop has the face of anyone not enrolled
  blurred; the clip stays behind sign in. Warnings go to the app (and Telegram if the society turns them on); info
  goes to the logs and the 9 AM daily summary.
* **An agent that checks before it bothers anyone.** The AI never watches the stream. OpenCV flags a moment; a
  verifier agent looks at it with OpenCV tools (zoom, brighten, optical flow, pose history, the path walked), then
  raises the alert with a grade, dismisses it, or asks a person (section 7).
* **Gate log.** Every vehicle in and out over a drawn line, with time, direction, plate text read with a confidence,
  plate colour by day (yellow means commercial, which covers cabs), a picture, the in and out paired by plate, an
  overstay alert, and a CSV export.
* **Staff register.** Consenting adult staff (maids, drivers, cooks, guards) are enrolled from three to five photos;
  first and last sighting each day from the cameras with the staff register on. Never children, never residents,
  deleted on exit.
* **Camera health.** Offline, covered, turned or blurred cameras raise an alert through the same watches as
  everything else; every society starts with that watch.
* **Plug in what exists.** RTSP from Hikvision, CP Plus and Dahua recorders (substream templates), the Pehra bridge
  for recorders behind the society's router (a small OpenCV program that pushes frames out, no port forwarding), any
  laptop or phone as a camera (open a link or scan a QR code; faces are blurred in the browser before a frame leaves
  the device), and uploaded recordings.
* **A real product around it.** Sign in, societies and roles (owner, admin, supervisor, viewer), billing per watched
  camera, a public site with pricing, terms, privacy and refunds, and a judges' demo with a private sandbox society
  for each visitor.

**Pricing.** Free: {{plans.free.cameras}} watched cameras, footage kept {{plans.free.days}} days. Society:
{{plans.society.month}} per watched camera a month (about {{plans.society.month_usd}}), or {{plans.society.year}} a
year (about {{plans.society.year_usd}}), footage kept {{plans.society.days}} days, with the gate log, staff register
and daily summary. Large sites from {{plans.large.min_cameras}} cameras: by agreement. A society picks which of its
cameras Pehra watches. Prices exclude GST and are charged in rupees through Polar, the merchant of record; dollar
amounts are approximate, at {{plans.rate}} to the dollar (RBI reference rate, {{plans.rate.date}}). What one camera
costs us on AWS is not measured yet (section 6), so the price is our call until the benchmark runs.

**What is new here.**

1. **Any event, compiled into generic OpenCV blocks, with an honest card.** No watch type is hard coded. A request
   becomes a composition of general signals (a directed line crossing, staying still by optical flow, a zone's state
   learned from two saved pictures, an attribute a reader attached, a look described in words), and the card says
   which parts are reliable, which approximate, which the AI checks every time, and which cannot be watched at all.
   A request never seen before ("someone carrying a ladder near the terrace door after 8 PM") compiled with no new
   code.
2. **OpenCV decides when the AI looks, and the AI looks with OpenCV.** The stream never goes to a model. OpenCV
   raises a candidate with its evidence; the verifier's next step depends on what its OpenCV tools return.
3. **Camera health is a signal, not a feature.** The same watch machinery that sees a person in a zone sees a
   covered or turned camera.
4. **Indian plates on society cameras with Model Zoo parts.** `LPD-YuNet` finds the plate, `warpPerspective`
   flattens it, `PP-OCRv5` reads it, an Indian plate grammar fixes look alike characters, reads are voted across
   frames, and the plate colour comes from HSV on the plate's background.
5. **Privacy built into the pixels.** Faces of anyone not enrolled are blurred in every Telegram still; a phone used
   as a camera blurs faces with OpenCV.js before sending; children are never enrolled and no watch may be about age,
   checked by words, patterns and a SigLIP 2 meaning check.

## 3. Architecture

![Pehra architecture](architecture.svg)

The diagram is the AWS deployment. The test server runs the same code in one container, with the differences listed
in the status table above. Mermaid source: [architecture.mmd](architecture.mmd).

**Cameras in.**

* **Pulled streams.** RTSP and RTSPS only (HTTP, HLS and other schemes are refused, because FFmpeg follows HTTP
  redirects past an address check). Addresses are checked against private, loopback, link local and mapped ranges
  before each connection (`netguard.py`), and sealed at rest with Fernet under `DATA_KEY`.
* **Pushed frames.** A camera device token (stored only as a SHA 256 hash, one per camera, rotatable) lets a laptop
  or phone page (`/cam/<token>`, its own small Vite entry) or the bridge push JPEG frames over a WebSocket.
* **Uploads** are scanned once in time chunks on every core, with tracks stitched across the chunk borders.

**FastAPI app** (one uvicorn worker). Sign in, the society's API (every route resolves its data from the session's
society), WebSockets for live views and pushed frames, the Telegram webhook, billing, and the judges' demo. It also
runs the camera manager, which writes everything the camera processes report.

**Camera pool.** Live cameras share camera processes, one per CPU by default (`CAMERA_PROCESSES=0`), assigned round
robin. A process loads each model once for all its cameras; each camera reads its source on its own thread and the
process analyses every camera with a due frame in turn. A camera process never opens the database: events, log rows,
candidates and health go to the app as messages, and the app writes them through the camera's own society. A dead
process is restarted with backoff and its cameras are given back. Demo sandbox cameras run on a camera process of
their own, so a crowd of judges never slows a society.

**Checks.** Candidates go to a priority pool (live critical first, then live, then uploads), where the verifier agent
checks them (section 7). Critical incidents are opened and posted before the check.

**Job worker.** A database backed queue with leases and fencing: Telegram posts, edits and reminders (rate limited per
chat; a failing group never blocks the others), the daily summary, the staff attendance roll up, stays at the gate
checked every minute, retention per society, the demo reset and the billing sweep. On AWS it is its own systemd
service; on the test server it runs inside the app.

**Storage.** SQLite in WAL mode through SQLAlchemy 2 and Alembic (Postgres ready, untested), copied continuously to S3
by Litestream on AWS. Pictures and clips in S3 on AWS, on local disk on the test server, every key under the
society's id.

**Tenancy.** Every row, picture key, face index, search index, event and socket belongs to one society. Child tables
point at their parents with the society in the key, so a row of society A cannot point at a camera of society B (the
database refuses it). `test_every_route_is_scoped` walks every API route with an id and fails on a route it does not
know.

**AI.** On AWS, Bedrock (Claude Haiku 5.5 through the global inference profile, the only model the instance role may
call). On the test server, OpenRouter with DeepSeek V4.1 Flash. Only for checking flagged moments, writing watches and
answering questions; never per frame; daily caps per society.

**Every replaceable part is a setting** (`registry.py`, `components.py`): storage, database URL, AI provider and model
per task, detector, pose, faces, embedder, OCR, plate detector, metrics, notifier, billing, sign in providers.
Thresholds live in one typed Tuning file (`tuning.py`, overridable per camera), data in `backend/watchful/data/*.json`,
prompts in `agent/prompts/*.md`. The product name comes from `brand.json`; the Python package keeps the name
`watchful` from the project Pehra grew out of.

**Reliability.**

* A camera process that dies is restarted, and while it is down the app itself raises its cameras' health watches.
* Pushed frames go to a bounded queue per process and are dropped while the process is down.
* An incident held for its AI check is posted as "not checked by the AI yet" after {{say:15 s}} (Tuning
  `alerts.hold_max_s`) if no verdict arrives.
* A crashed job worker's job runs again; a late worker cannot overwrite it.
* If the AI fails, times out or hits its daily cap, a critical moment still alerts, marked unverified.

## 4. OpenCV 5 implementation

Every pixel step goes through OpenCV 5: `opencv-python-headless` 5.0.0.93 in development and on the test server,
COOL's build on Graviton on AWS, and OpenCV.js 5.0 on the device page. The exceptions, by design: FFmpeg encodes the
incident clips (no analysis), and the browser draws the live overlay on a canvas from boxes the server sent.

### 4.1 Per frame

On each analysed frame ({{say:5 frames a second}} by default, `ANALYSIS_FPS`, at 960 px wide):

1. **Decode and resize.** `cv2.VideoCapture` with FFmpeg (RTSP over TCP, open and read timeouts) or `cv2.imdecode`
   for pushed JPEG; `cv2.resize` with `INTER_AREA` to the analysis width.
2. **Night mode.** When the frame is dark by its HSV value, CLAHE on the lightness of Lab.
3. **Camera health.** Every few seconds a small grey copy is compared with a reference view: `meanStdDev` tells a
   covered lens (the picture went flat), the variance of the Laplacian a blurred one, and ORB keypoints with a ratio
   test (`BFMatcher.knnMatch`), a `USAC_MAGSAC` homography (`findHomography`) and `perspectiveTransform` of the centre
   a turned one. A problem must hold for a while before it counts.
4. **Motion gate.** MOG2 background subtraction on a blurred 320 px copy, opening and dilation with an elliptical
   kernel, contours. A still frame with nothing tracked skips the detector.
5. **Detection.** YOLOX nano through `cv2.dnn` (`readNetFromONNX` on the engine `DNN_ENGINE` picks), with
   `NMSBoxesBatched`. While only tracked objects move, the detector runs every second frame; new motion outside a
   tracked box forces it.
6. **Tracking.** A `cv2.KalmanFilter` per track and three association passes: two by IoU against the prediction, then
   a third by distance from the prediction over the time since the track was last seen (in body heights a second,
   faster for vehicles), size and class, with ties broken by an HSV clothing histogram (`calcHist`, `compareHist`
   with Bhattacharyya). The third pass keeps ids at the low frame rates a shared server delivers (section 8.3).
7. **Zones and lines.** Areas by `pointPolygonTest` on the foot point; directed lines with hysteresis, so a crossing
   is counted once, and a guard against two tracks swapping ids at the line.
8. **Only when a watch needs it.** MediaPipe pose through `cv2.dnn` on a crop turned upright with
   `getRotationMatrix2D` and `warpAffine`; YuNet (`FaceDetectorYN`) and SFace (`FaceRecognizerSF`) on cameras with
   the staff register; SigLIP 2 image vectors of a person's sharpest crop (Laplacian variance) every few seconds for
   watches described in words; plate reads on vehicles near a gate line.
9. **Signals and rules.** The watch engine reads the tracks and the scene (section 4.2) and raises a candidate with
   its evidence: frames around the moment with the zones and the box drawn (`polylines`, `rectangle`), the track's
   path and poses, and the best crop.

### 4.2 Watches for any event

A watch is data: a subject (classes, an enrolled person or anyone unknown, or a look described in words), signals
with conditions, how they combine (all, any, or a weighted score), hours, severity, an output (an alert, or a row in a
log) and a question for the verifier. The compiler prompt is built from the signal catalog, so a new signal reaches
it with no prompt edit. Nothing about pools, gates or cabs is in the code; a camera's purpose only suggests texts
from `data/purposes.json`.

| Signal | What it measures, and the OpenCV behind it | Tier |
| --- | --- | --- |
| `present`, `entered`, `exited`, `in_zone`, `dwell_s`, `count`, `zones_visited`, `returns` | Tracks against zones (`pointPolygonTest` on the foot point) | A |
| `crossed` | A directed crossing of a drawn line, with hysteresis and an id swap guard | A |
| `stationary_s` | Seconds a box has stayed put; on the slow tier a CLAHE edge picture of the spot keeps the memory while it still matches (`matchTemplate`) | A |
| `still_s` | Seconds a body has not moved by itself: DIS optical flow (MEDIUM preset on a small crop) inside the box compared with the flow in a ring around it, so a body drifting in moving water still reads as still | B |
| `vanished_in_zone` | A track ended inside a zone away from the frame edges, and nobody reappeared nearby within the hold | B |
| `alone` | The only one of its class in a zone, with nobody within a distance measured in body heights | A |
| `zone_state` | The state of a fixed thing (gate open or closed, pool cover on or off) by its nearest saved example: a blurred, CLAHE and Sobel edge picture, blended with SigLIP 2 image vectors; day and night examples apart | B |
| `looks_like`, `zone_looks_like` | A person or a zone against a text and its contrast, through SigLIP 2's own calibrated probability | B |
| `attribute`, `inside_s` | What a reader attached (plate text, plate colour), and how long a vehicle has been inside since it came in over a line | B |
| `speed`, `posture`, `hands_in_zone_s`, `climbing` | Body heights a second; upright, falling or fallen from pose; hands in a zone; climbing | B |
| `camera_health` | Live, offline, covered, turned or blurred (section 4.1, step 3) | A |

**The compiler** (`agent/compiler.py`) asks the AI for a watch, then checks it in code: unknown signals go back to the
AI and then become a "cannot"; contradictions go back; zone and line names are fixed to the drawn ones; a tier can
never claim better than its signal's own tier; setup tasks (draw this line, save two pictures of the gate open at
night) are written by code, not the AI. The card shows what will be watched and how reliably (A reliable, B
approximate, C the AI checks every time, X not watched), what cannot be watched with the nearest alternative, and the
setup tasks.

**The backtest** replays a new watch over the last 24 hours of the camera's event index before it goes live, and says
"Would have fired {{say:3 times}}", or "About 1 visit, assuming staying still held" when a signal needs pixels the index does
not keep.

With the real AI (DeepSeek V4.1 Flash), {{build.compile.generic}} of {{build.compile.requests}} plain words requests
about the pool, gates, lobby, generator smoke, a ladder and a covered camera compiled into these blocks with no new
code, in {{build.compile.fastest_s|bare}} to {{build.compile.slowest_s}} each. "Tell me when a child is alone at the pool"
became "someone alone at the pool" plus a plain refusal of the age part.

### 4.3 Gate log and plates

On a camera with plate reading on, each vehicle track is read at most twice, only when its box is big enough and, on a
camera with lines, while its foot point is near a line. One read:

1. `LPD-YuNet` (OpenCV Model Zoo) through `cv2.dnn` finds plate quadrilaterals; impossible boxes are dropped before
   NMS (the model emits a huge off image box on some inputs).
2. Each quad is grown and flattened with `getPerspectiveTransform` and `warpPerspective` to a plate 96 px high; a quad
   that covers too little of its own outline (a sliver) is replaced by its bounding box.
3. A plate narrower than a set aspect is cut into two lines by horizontal projection after Otsu (`threshold`).
4. `PP-OCRv5` mobile recognition reads each line through `cv2.dnn` (its CTC output is decoded by hand, since
   `TextRecognitionModel` returns an empty string for it). When the box read is not clean, the `PP-OCRv3` DB detector
   (`cv2.dnn.TextDetectionModel_DB`) finds the text rows around the box and the cheaper read wins; with no plate box
   it reads the whole crop.
5. An Indian plate grammar (`data/plates.json`: the state codes, the usual and BH layouts) swaps look alike characters
   by position (0 and O, 8 and B, 5 and S) and scores each read; reads costing too much are refused.
6. Reads are voted across frames, and identical crops count once. The confidence folds the grammar cost, the box score
   and agreement; under {{say:0.52}} (Tuning `plates.min_conf`) the text is left unread.
7. Plate colour comes from the HSV median of the plate's background with the text removed by Otsu: white, yellow,
   green, black, or unknown on an infrared or dark frame. Never a colour from the vehicle's body.

A vehicle's crossing waits briefly for its reads, then becomes a passage. An out pairs with the latest unpaired in of
the same plate within three days, never on a guess. The built in overstay is a watch like any other: plate colour
yellow and `inside_s` over two hours (Tuning `ledger.overstay_s`). In {{build.plates.fuzzed}} fuzzed plates with the
last character cut off, no wrong read reached a confidence above {{build.plates.fuzzed_max_conf}}, under the
{{say:0.52}} needed to keep a text.

### 4.4 Staff register

`POST /api/staff` takes a name, a role, a consent tick and three to five photos. Each photo must hold exactly one face
(YuNet), like the others (SFace cosine); each becomes a face template and the photo itself is never stored. A name is
given at an SFace cosine of {{say:0.45}} on one look, or {{say:0.363}} on three looks of the same person, each ahead
of the next person by a margin; from {{say:0.30}} a match is only "possible" and never a name (thresholds from our
research, in Tuning `faces`). Attendance is the first and last sighting each day in the society's time zone, rolled
up again from stored events every night. Deleting a staff member deletes the templates and tells every camera process
at once, which forget the name on every open track.

### 4.5 Privacy in the pixels

* **Telegram stills** (`alerts/crops.py`): YuNet finds faces and SFace keeps only a face enrolled for that very person
  sharp; every other person's head is pixelated (`resize` with `INTER_AREA` down and `INTER_NEAREST` up, then a
  Gaussian blur) where MediaPipe pose puts it, or the whole person when pose finds no head. A still with no person box
  runs the person detector first. If the face models fail to load, the alert goes as text.
* **The device page** (`web/src/cam/`): OpenCV.js 5.0 runs `cv.FaceDetectorYN` with YuNet on a 320 px copy of each
  frame in the browser; each face is grown, held a moment and replaced by an opaque mosaic before the JPEG is made. A
  frame is never sent unless detection ran on it, and nothing is sent until the server's hello agrees with the notice
  on screen. In OpenCV.js, YuNet took {{build.device.yunet_js_320_ms}} at 320 px against
  {{build.device.yunet_js_640_ms}} at 640 px, which set the size.
* **The device link's QR code** is drawn by `cv2.QRCodeEncoder` as one SVG path. Our test decodes it back with
  `cv2.QRCodeDetectorAruco`, which read all {{build.device.qr_aruco_read}} random links where the classic
  `QRCodeDetector` missed {{build.device.qr_classic_missed}}.

### 4.6 Every OpenCV API we call, and why

Found with `git grep -n "cv2\." backend/watchful bridge deploy` and `grep -rn "cv\." web/src/cam`.

**Video and frames**

| API | Where | Why |
| --- | --- | --- |
| `VideoCapture` (FFmpeg; `CAP_PROP_OPEN_TIMEOUT_MSEC`, `CAP_PROP_READ_TIMEOUT_MSEC`, `CAP_PROP_POS_MSEC`, `CAP_PROP_FPS`, `CAP_PROP_N_THREADS`) | `ingest/sources.py`, `vision/scan.py`, `bridge/pehra_bridge.py`, `demo.py`, `evidence.py` | RTSP from recorders, demo loops, chunked scans that seek by time, frames cut for the AI and the demo |
| `imdecode`, `imencode` (JPEG) | wherever frames travel | Pushed frames in; keyframes, stills and live frames out |
| `resize` (`INTER_AREA`, `INTER_CUBIC`, `INTER_LINEAR`, `INTER_NEAREST`) | everywhere | Analysis copies (area), zoom for the agent and small faces (cubic), mosaics (nearest) |
| `cvtColor` (grey, HSV, Lab, RGB) | everywhere | Grey for flow and features, HSV for darkness, clothes and plates, Lab for CLAHE |
| `setNumThreads` | `pool.py`, `vision/scan.py` | One OpenCV thread per camera process; the processes fill the CPUs |
| `getBuildInformation`, `__version__`, `videoio_registry` | `api/app.py` (`/healthz`), `deploy/cv2_info.py` | Report the build, KleidiCV and the video backends; prove which OpenCV ran (section 6) |

**Motion, tracking and flow**

| API | Where | Why |
| --- | --- | --- |
| `createBackgroundSubtractorMOG2`, `GaussianBlur`, `morphologyEx` (`MORPH_OPEN`), `getStructuringElement`, `dilate`, `countNonZero`, `findContours`, `contourArea`, `boundingRect` | `vision/motion.py` | Idle frames skip the detector; new motion outside tracked boxes forces it |
| `KalmanFilter` | `vision/track.py` | A predicted box per track for association |
| `calcHist`, `normalize`, `compareHist` (`HISTCMP_BHATTACHARYYA`) | `vision/appearance.py` | Clothes break ties between two people in the tracker and carry a name the same day |
| `pointPolygonTest` | `vision/zones.py` | Zones on the foot point |
| `DISOpticalFlow_create` (MEDIUM in `still_s`, FAST in the agent's `flow` tool) | `rules/signals.py`, `agent/tools.py` | A body's own movement against the water around it; how fast a body moves down |

**Deep learning (`cv2.dnn`) and faces**

| API | Where | Why |
| --- | --- | --- |
| `dnn.readNetFromONNX` with `ENGINE_AUTO`, `ENGINE_NEW`, `ENGINE_CLASSIC` | `vision/models.py` | Every model loads through one function; `PP-OCRv5` recognition, which the classic engine cannot read, falls back to the new engine |
| `dnn.blobFromImage`, `Net.setInput`, `Net.forward` | `vision/detect.py`, `vision/pose.py`, `vision/embed.py`, `vision/plates.py`, `vision/ocr.py` | YOLOX, MediaPipe pose, SigLIP 2 image and text towers, `LPD-YuNet`, `PP-OCRv5` |
| `dnn.NMSBoxesBatched`, `dnn.NMSBoxes` | `vision/detect.py`, `vision/plates.py` | Per class suppression for YOLOX; plate boxes (with the Zoo demo's suppression bug fixed) |
| `dnn.TextDetectionModel_DB`, `dnn.TextRecognitionModel` | `vision/ocr.py` | `PP-OCRv3` text rows on plates; CRNN stays selectable |
| `FaceDetectorYN`, `FaceRecognizerSF` (`FR_COSINE`) | `vision/faces.py`, `alerts/crops.py` | YuNet faces for blurring and naming; SFace templates for consenting staff only |

**Geometry and features**

| API | Where | Why |
| --- | --- | --- |
| `ORB_create`, `BFMatcher` (`NORM_HAMMING`, `knnMatch`), `findHomography` (`USAC_MAGSAC`), `perspectiveTransform` | `vision/tamper.py` | A turned camera: the reference view no longer maps onto the frame |
| `getPerspectiveTransform`, `warpPerspective` | `vision/quality.py` | Plates flattened before OCR |
| `getRotationMatrix2D`, `warpAffine`, `invertAffineTransform` | `vision/pose.py` | Pose on a crop turned upright, landmarks mapped back |
| `minAreaRect`, `boxPoints` | `vision/plates.py` | Plate quadrilaterals from text rows |
| `matchTemplate` (`TM_CCOEFF_NORMED`) | `rules/zonestate.py` | A parked thing's spot still matches its edge picture |

**Image processing and drawing**

| API | Where | Why |
| --- | --- | --- |
| `createCLAHE` | `vision/quality.py`, `rules/zonestate.py`, `agent/tools.py` | Night mode, zone state edge pictures, the agent's `enhance` |
| `Laplacian` | `vision/quality.py`, `vision/tamper.py` | The sharpest crop for faces, vectors and the AI; a blurred camera |
| `Sobel`, `magnitude`, `GaussianBlur`, `fillPoly`, `bitwise_and`, `meanStdDev` | `rules/zonestate.py`, `vision/tamper.py` | Edge pictures of a zone, masked to it; night by saturation; a covered camera |
| `threshold` (Otsu) | `vision/plates.py` | Splitting two line plates; removing the text before judging plate colour |
| `fastNlMeansDenoisingColored`, `addWeighted` | `agent/tools.py` | The agent's denoise and unsharp mask |
| `polylines`, `circle`, `rectangle`, `putText`, `getTextSize` | `evidence.py`, `agent/tools.py`, `vision/pipeline.py`, `logs/demo.py` | Zones and the box on the AI's frames; the path walked; the plates drawn for the demo's gate log |
| `QRCodeEncoder` | `ingest/push.py` | The device link QR code for a phone |
| OpenCV.js `cv.FaceDetectorYN`, `cv.resize`, `cv.cvtColor` | `web/src/cam/faceBlur.ts` | Faces blurred on the phone or laptop before sending |

### 4.7 Models and licences

Every model allows a paid service. `scripts/fetch_assets.py` downloads them and checks each SHA 256 listed in
`backend/watchful/vision/models.py`.

| Model | Use | Licence |
| --- | --- | --- |
| YOLOX nano (Megvii; 320 and 384 px exports of the same weights) | People and vehicles | Apache 2.0 |
| MediaPipe pose (OpenCV Model Zoo) | Falls, hands, where a head is for blurring | Apache 2.0 |
| YuNet (OpenCV Model Zoo) | Faces, for blurring and for staff | MIT |
| SFace (OpenCV Model Zoo) | Staff face templates | Apache 2.0 |
| `LPD-YuNet` (OpenCV Model Zoo, by watrix.ai) | Plate boxes | Apache 2.0 |
| `PP-OCRv3` English DB detection and CRNN (OpenCV Model Zoo) | Text rows; a selectable recogniser | Apache 2.0 |
| `PP-OCRv5` mobile English recognition (PaddlePaddle ONNX) | Reading plates | Apache 2.0 |
| SigLIP 2 base patch32 256 (ONNX by `onnx-community`) | Looks described in words, zone pictures, search, the age meaning check | Apache 2.0 |
| OpenCV.js 5.0 (`@techstark/opencv-js 5.0.0-release.1`) | Face blur on the device page | Apache 2.0 |

Not used, because their licences do not allow a paid service: Ultralytics YOLO (AGPL), YOLO World (GPL).

## 5. AWS deployment

Waiting on the owner's go ahead. Everything in this section is built and tested without AWS (`cfn-lint`, moto and a fake
`aws` command), and the results of a real deploy fill the table at its end.

One command, `deploy/deploy.sh`, builds the whole stack in Mumbai (`ap-south-1`) from `deploy/cloudformation.yaml`,
puts settings and secrets in SSM Parameter Store, ships the committed code through S3 and SSM Run Command, checks the
result, and writes `eval/results/aws.json` from what it checked. There is no SSH and no key pair.

| Part | What it is |
| --- | --- |
| Compute | One EC2 c8g.xlarge (Graviton4, 4 vCPUs, 8 GiB; large, 2xlarge and 4xlarge allowed) from the COOL image, IMDSv2 only, encrypted gp3, an Elastic IP, ports 80 and 443 in, egress only on 443, 80 and 554 (RTSP), instance recovery |
| OpenCV | COOL (OpenCV 5 built with Arm's KleidiCV) in COOL's own Python; the deploy fails unless `cv2` is loaded from under `/opt/cool` with KleidiCV in its build and decodes H.264 and H.265 clips like the ones recorders send |
| Web | Caddy with automatic TLS in front of uvicorn; WebSockets in their own handle; an access log with tokens, secrets and query strings removed |
| Services | systemd units for the app, the job worker and Litestream, each started through `deploy/ssm_env.py` (loads `/<stack>/*` from SSM, fails closed), each requiring the egress rules unit |
| Egress rules | `deploy/egress.nft`: the app's Unix user cannot open new connections to private, loopback, link local, CGNAT or ULA addresses (DNS and the instance metadata service excepted), so a camera address that redirects cannot reach the server's own network |
| Storage | S3 with versioning, Block Public Access, encryption, TLS only, lifecycle rules; Object Lock is turned on for the bucket, with no default retention |
| Database | SQLite on the instance, replicated to the bucket by Litestream; a new instance restores it first |
| AI | Bedrock, Claude Haiku 5.5 through the global inference profile; the role may invoke only that model |
| Observability | CloudWatch log groups (app 30 days, access 90 days); metrics for HTTP requests and 5xx from Caddy's log, frames a second and milliseconds a frame per camera process, camera processes down, AI calls today, alerts, worker heartbeat, disk and memory; a dashboard; six alarms (5xx rate, CPU, disk, worker down, camera processes down, instance check) by email through SNS; a monthly budget alarm |
| Security | The role reaches only this bucket, these log groups, this model and parameters under `/pehra/` (an explicit deny keeps parameter reads inside that path, since the Session Manager policy allows more); secrets in SSM SecureString; `DATA_KEY` and `SESSION_SECRET` made once and never overwritten |

The deploy proves itself before it finishes: `deploy/cv2_info.py --require-cool` checks the file `cv2` was loaded
from, KleidiCV in the build and the two video clips; `/healthz` answers with the OpenCV version, whether KleidiCV is in
the build and whether the job worker beat recently; `deploy/check_memory.py` adds up the app, its camera processes,
clip encoders and the worker, and fails above the budget.

**The deployment, as checked by the deploy** (`eval/results/aws.json`):

| Check | Result |
| --- | --- |
| Region and instance | {{aws.region}}, {{aws.instance_type}} |
| Deployed, commit | {{aws.deployed_at}}, {{aws.code}} |
| OpenCV version, and as the running app reports it | {{aws.opencv}}, {{aws.healthz_opencv}} |
| `cv2` loaded from | {{aws.cv2_file}} |
| Loaded from under `/opt/cool` (the proof of COOL) | {{aws.cool}} |
| KleidiCV in the build, by `cv2_info.py` and by `/healthz` | {{aws.kleidicv}}, {{aws.healthz_kleidicv}} |
| Job worker heartbeat fresh | {{aws.worker_beat}} |
| Memory of the app, camera processes and worker, and the budget | {{aws.memory_mb}} of {{aws.memory_budget_mb}} (inside it: {{aws.memory_ok}}) |

**Cost.** Estimated at about {{build.estimates.aws_month_usd}} (about {{cost.aws_month_inr}}) a month from AWS list
prices for Mumbai: c8g.xlarge on demand ([EC2 pricing](https://aws.amazon.com/ec2/pricing/on-demand/)), COOL's fee
([its Marketplace listing](https://aws.amazon.com/marketplace/pp/prodview-fdvbfiewzuehs)), EBS, a public IPv4 address
and CloudWatch, before Bedrock, S3 and transfer. It is an estimate, not a bill.

**Reproducing it.** The README's "Deploy to AWS" lists the owner's steps: an account on the Paid plan, the COOL
subscription, Bedrock model access, `deploy/params.env`, then `deploy/deploy.sh --dry-run` and `deploy/deploy.sh`.

## 6. COOL on AWS Graviton

Planned. Nothing in this section has run on AWS yet. The benchmark is built and tested and has run only on our x86 dev
box; until `eval/bench_aws.sh --yes-spend` runs, every AWS number below reads "not measured yet".

**Setup.** COOL (Cloud Optimized OpenCV Library) for Graviton4 from
[AWS Marketplace](https://aws.amazon.com/marketplace/pp/prodview-fdvbfiewzuehs): an Ubuntu image with OpenCV 5.0 built
with Arm's KleidiCV, and Python environments under `/opt/cool/venvs` ([OpenCV's COOL page](https://opencv.org/cool/)).

**What the code does on Graviton.**

* **The whole live pipeline runs in COOL's Python.** `deploy/remote_setup.sh` installs our requirements into
  `/opt/cool/venvs/python_3.12` with `opencv-python` left out, so every camera process, the scan workers, the agent's
  tools and the job worker's stills import COOL's `cv2`.
* **COOL is proven by a file path, not by a word in the build.** KleidiCV alone proves nothing, since stock OpenCV
  builds for Arm can carry it. `deploy/cv2_info.py --require-cool` fails the deploy unless `cv2` was loaded from under
  `/opt/cool`; the deploy writes the loaded file into `aws.json` (section 5) and every benchmark file records it.
* **KleidiCV detection.** `/healthz` reports `kleidicv` from `cv2.getBuildInformation()`, and the benchmark records
  it per run.
* **The DNN engine.** Every model loads through `readNetFromONNX` on the engine `DNN_ENGINE` names (`auto` by
  default), the same on x86 and Arm; `PP-OCRv5` recognition always uses the new engine. One runtime for every model
  means COOL's build serves the whole pipeline.
* **Sizing for c8g.** One camera process per vCPU (`CAMERA_PROCESSES=0`), one OpenCV thread each
  (`CAMERA_THREADS=1`), `MALLOC_ARENA_MAX=2` in the units, models loaded once per process, cameras that need the
  image model kept together, and a memory budget of {{say:3,072 MB}} (Tuning `resources.memory_budget_mb`) checked by
  the deploy. The default is c8g.xlarge; the template also takes c8g.large, 2xlarge and 4xlarge.
* **What COOL can speed up.** Every frame is resized twice (`INTER_AREA` to 960 and 320 px), converted, blurred, run
  through MOG2 and morphology, and often put through CLAHE or a warp: the imgproc functions COOL targets. The DNN
  forwards (YOLOX, pose, SigLIP) are most of a busy frame's time, and we know of no claimed COOL gain there. The
  benchmark measures the whole frame, so it shows the gain a society would see, not a gain per function.

**Method.** `eval/bench_aws.sh --yes-spend` starts temporary instances in Mumbai, each with a hard time cap, runs
`eval/bench.py` (the live camera pool with the app's defaults on the Plaza clip, a stand in AI, no real AI calls),
copies the results back and terminates everything it started:

1. c8g.large from the COOL image: COOL's own Python (`c8g_cool`), then stock `opencv-python-headless` 5.0.0.93 in a
   separate environment on the same instance (`c8g_stock`), so only OpenCV differs;
2. c7i.large (Intel, x86) with stock OpenCV 5.0.0.93 (`c7i_stock`), the x86 instance of the same size.

Each setup runs twice: without a watch described in words (`_no_text`, the case that fits in 4 GiB on the dev box),
then with the app's defaults and such a watch on every camera.

| Without a watch in words | Dev box (x86, not a target) | c8g.large, COOL | c8g.large, stock OpenCV 5 | c7i.large (x86), stock |
| --- | --- | --- | --- | --- |
| Run on, commit | {{bench.devbox_no_text.date}}, {{bench.devbox_no_text.code}} | {{bench.c8g_cool_no_text.date}}, {{bench.c8g_cool_no_text.code}} | {{bench.c8g_stock_no_text.date}}, {{bench.c8g_stock_no_text.code}} | {{bench.c7i_stock_no_text.date}}, {{bench.c7i_stock_no_text.code}} |
| CPU | {{bench.devbox_no_text.cpu}} | {{bench.c8g_cool_no_text.cpu}} | {{bench.c8g_stock_no_text.cpu}} | {{bench.c7i_stock_no_text.cpu}} |
| OpenCV; COOL; KleidiCV | {{bench.devbox_no_text.opencv}}; {{bench.devbox_no_text.cool}}; {{bench.devbox_no_text.kleidicv}} | {{bench.c8g_cool_no_text.opencv}}; {{bench.c8g_cool_no_text.cool}}; {{bench.c8g_cool_no_text.kleidicv}} | {{bench.c8g_stock_no_text.opencv}}; {{bench.c8g_stock_no_text.cool}}; {{bench.c8g_stock_no_text.kleidicv}} | {{bench.c7i_stock_no_text.opencv}}; {{bench.c7i_stock_no_text.cool}}; {{bench.c7i_stock_no_text.kleidicv}} |
| Cameras, vCPUs | {{bench.devbox_no_text.cameras}}, {{bench.devbox_no_text.vcpus}} | {{bench.c8g_cool_no_text.cameras}}, {{bench.c8g_cool_no_text.vcpus}} | {{bench.c8g_stock_no_text.cameras}}, {{bench.c8g_stock_no_text.vcpus}} | {{bench.c7i_stock_no_text.cameras}}, {{bench.c7i_stock_no_text.vcpus}} |
| Analysed frames a second, all cameras | {{bench.devbox_no_text.fps_total|bare}} | {{bench.c8g_cool_no_text.fps_total|bare}} | {{bench.c8g_stock_no_text.fps_total|bare}} | {{bench.c7i_stock_no_text.fps_total|bare}} |
| The same per camera, median | {{bench.devbox_no_text.fps_per_camera|bare}} | {{bench.c8g_cool_no_text.fps_per_camera|bare}} | {{bench.c8g_stock_no_text.fps_per_camera|bare}} | {{bench.c7i_stock_no_text.fps_per_camera|bare}} |
| The same per vCPU | {{bench.devbox_no_text.fps_per_vcpu|bare}} | {{bench.c8g_cool_no_text.fps_per_vcpu|bare}} | {{bench.c8g_stock_no_text.fps_per_vcpu|bare}} | {{bench.c7i_stock_no_text.fps_per_vcpu|bare}} |
| Frame latency, median and 95th percentile (ms) | {{bench.devbox_no_text.latency_p50|bare}}, {{bench.devbox_no_text.latency_p95|bare}} | {{bench.c8g_cool_no_text.latency_p50|bare}}, {{bench.c8g_cool_no_text.latency_p95|bare}} | {{bench.c8g_stock_no_text.latency_p50|bare}}, {{bench.c8g_stock_no_text.latency_p95|bare}} | {{bench.c7i_stock_no_text.latency_p50|bare}}, {{bench.c7i_stock_no_text.latency_p95|bare}} |
| Memory, median and peak (MB) | {{bench.devbox_no_text.memory_total|bare}}, {{bench.devbox_no_text.memory_peak|bare}} | {{bench.c8g_cool_no_text.memory_total|bare}}, {{bench.c8g_cool_no_text.memory_peak|bare}} | {{bench.c8g_stock_no_text.memory_total|bare}}, {{bench.c8g_stock_no_text.memory_peak|bare}} | {{bench.c7i_stock_no_text.memory_total|bare}}, {{bench.c7i_stock_no_text.memory_peak|bare}} |
| Cameras this machine gives {{say:2 frames a second}} | {{bench.devbox_no_text.cameras_at_2fps}} | {{bench.c8g_cool_no_text.cameras_at_2fps}} | {{bench.c8g_stock_no_text.cameras_at_2fps}} | {{bench.c7i_stock_no_text.cameras_at_2fps}} |
| Compute per camera a month at that rate, priced as a c8g.large | {{bench.devbox_no_text.cost.c8g_large.at_2fps.inr}} | {{bench.c8g_cool_no_text.cost.c8g_large.at_2fps.inr}} | {{bench.c8g_stock_no_text.cost.c8g_large.at_2fps.inr}} | {{bench.c7i_stock_no_text.cost.c8g_large.at_2fps.inr}} |

COOL over stock OpenCV on the same Graviton instance, all cameras' frames a second:
{{bench.speedup.cool_over_stock_no_text}}; c8g with COOL over c7i with stock OpenCV:
{{bench.speedup.graviton_over_x86_no_text}}. With a watch in words on every camera: {{bench.speedup.cool_over_stock}}
and {{bench.speedup.graviton_over_x86}}; c8g.large with COOL then gives {{bench.c8g_cool.fps_per_camera}} per camera
in {{bench.c8g_cool.memory_total}}.

The dev box column is there to show the method, not as a target: a 2013 Xeon without AVX2, sharing a host of
{{bench.devbox_no_text.host_cores}} cores whose load over a minute stayed near {{bench.devbox_no_text.host_load}}
during the run, with other work on it. Its cost row applies the dev box's rate to a c8g.large's price; it is not a
Graviton measurement.

**What to expect, honestly.** The Marketplace listing says COOL was benchmarked on imgproc, core and I/O functions
against default OpenCV 5.0.0, with KleidiCV and tuned parallel code; that is the vendor's claim, not checked by us.

**Reproduce.** `eval/bench_aws.sh` prints the plan and its expected cost and starts nothing;
`eval/bench_aws.sh --yes-spend` runs it. It needs an AWS account on the Paid plan and the COOL subscription, not the
deployed app. `python3 docs/fill.py` then fills this table.

## 7. The agent (Agentic Vision)

![Agent workflow](agent-workflow.svg)

Mermaid source: [agent-workflow.mmd](agent-workflow.mmd). The loop: OpenCV perceives every frame; rules over its
signals decide whether a moment is worth a look; the verifier agent looks with OpenCV tools until it can decide; the
action is an incident graded critical, warning or info, a dismissal, or a question to a person; and the people in the
society's Telegram group close the loop with Acknowledge or False alarm, which changes what is posted next.

**Perception (OpenCV 5).** Section 4.1: detection, tracks, zones, lines, flow, pose, faces, plates, zone states and
camera health, on every analysed frame, on the server. The AI never sees the stream.

**Decision, in code first** (free, no AI):

* A watch's conditions hold, so a candidate is raised with its evidence: the reasons in words ("came into pool area",
  "still for {{say:20 s}}"), the signal values, frames around the moment with the zones and the box drawn, the best crop, the
  track's path and poses.
* A candidate whose subject was named by an enrolled face is an alert at once, with no AI step.
* A camera health candidate is filed at once, with no AI step and no clip.
* A critical candidate opens its incident and posts to Telegram before the check, saying "Possible:" and "Checking
  now"; the verdict edits the message.
* After the AI cleared the same camera and watch, or a person marked it a false alarm, within the grouping window, a
  new critical trigger is held and checked first, and posts only if the check says it is real.

**Decision, the verifier with OpenCV tools** (`agent/verifier.py`, `agent/tools.py`, prompt
`agent/prompts/verify.md`): the model sees the watch, its question, what OpenCV saw and the pictures, and replies with
one JSON tool call per step. Each tool result goes back as the next message, with any new picture, so the next step
depends on it. At most {{say:6}} steps and {{say:45 s}}; at most {{say:6}} pictures in view.

| Tool | What it runs |
| --- | --- |
| `get_frames(from_s, to_s, n)` | Up to four frames around the moment, with the zones and the box drawn (`polylines`) |
| `crop_zoom(at_s)` | The flagged object, enlarged twice with `INTER_CUBIC` and sharpened with an unsharp mask (`GaussianBlur`, `addWeighted`) |
| `enhance(image, kind)` | Brighten (CLAHE on the lightness of Lab), denoise (`fastNlMeansDenoisingColored`) or sharpen any picture it has |
| `motion_trail()` | The path the object walked, drawn on the latest frame (`polylines`, `circle`) |
| `flow(from_s, to_s)` | DIS optical flow inside the object's box: body heights a second downward and sideways |
| `pose_history(seconds)` | The person's torso angle over time from MediaPipe pose (0 upright, 90 lying) |
| `raise_alert(summary, severity, reason)`, `dismiss(reason)`, `ask_human(question)`, `cannot_judge(reason)` | Finish: real, with a grade and why; not real; a person should decide; or the question needs an age |

**Where an OpenCV result changes the next action.**

| OpenCV result | What happens next | Shown by |
| --- | --- | --- |
| A watch's signals hold on a track or the scene | A candidate is raised; otherwise nothing is sent anywhere | `rules/engine.py`; `test_signals_v2.py`, `test_watch_review.py` |
| YuNet and SFace name an enrolled person | An alert with no AI step | `test_agent.py::test_face_match_skips_ai` |
| ORB, MAGSAC, the Laplacian or `meanStdDev` say covered, turned or blurred; no frames say offline | A warning incident at once, no AI; resolved when the camera is live again | `test_health.py` |
| The pictures the model sees are dark, small or blurry | The prompt tells the model to look closer before deciding: `enhance` or `crop_zoom`, then it decides on the result | `verify.md`; `test_agent.py::test_zoom_then_alert`, `::test_enhance_then_dismiss` |
| `flow` or `pose_history` report fast downward motion or a lying torso | The model reads those numbers in its next step before its verdict | `agent/tools.py`; `test_agent.py::test_step_limit_falls_back` runs `pose_history` |
| The AI cleared this camera and watch a moment ago | A new critical trigger is held and checked before anything is posted | `test_incidents.py::test_an_ai_dismissal_holds_later_triggers_until_checked` |
| The AI's check says real on an incident a guard just marked false | A new incident is opened and posted; a tap never silences a later AI alert | `test_incidents.py::test_a_false_alarm_never_silences_a_later_real_alert` |
| A plate read gives a yellow plate, and `inside_s` passes two hours | An overstay warning incident; the gate log marks the passage | `test_ledger.py` |
| SigLIP 2 says a look text means a child | The text is refused; the watch is saved without that part, or not at all | `test_age_guard.py` |
| OpenCV.js cannot blur (the model fails to load, or blur fails three times running) | The device stops sending and says so | `web/e2e/cam.spec.ts` |

**Traces.** Every check stores its trace (step, tool, arguments, the model's reason, the tool's result, milliseconds)
with the alert, and the incident page shows it. The tests above drive the loop with a scripted model and assert the
trace: in `test_zoom_then_alert` the model's first step is `crop_zoom`, whose result goes back to it, and its second step
raises the alert; in `test_enhance_then_dismiss` it brightens the frame with CLAHE first and then dismisses.

**A real trace.** `eval/agent_trace.py` sends one flagged moment of the Plaza clip (a person walking into the left
walkway at {{agent.plaza.moment_s}}, found by YOLOX and the tracker) to the app's own verifier with the real model
({{agent.plaza.model}}, {{agent.plaza.date}}): twice as filmed, and twice with the frames dimmed to
{{agent.plaza_dark.dark}} of their brightness, as a camera sees at dusk. Every run raised the critical alert; what the
model looked at first changed with the pictures:

| Run | Pictures | Steps, in order |
| --- | --- | --- |
| 1 | as filmed | {{agent.plaza.r0.tools}} |
| 2 | as filmed | {{agent.plaza.r1.tools}} |
| 3 | dimmed | {{agent.plaza_dark.r0.tools}} |
| 4 | dimmed | {{agent.plaza_dark.r1.tools}} |

Run 4, step by step (`eval/results/agent-trace-plaza_dark.json`):

| Step | Tool | The model's reason | What OpenCV returned | Time |
| --- | --- | --- | --- | --- |
| 1 | {{agent.plaza_dark.r1.s0.tool}} | {{agent.plaza_dark.r1.s0.why}} | {{agent.plaza_dark.r1.s0.result}} | {{agent.plaza_dark.r1.s0.ms}} |
| 2 | {{agent.plaza_dark.r1.s1.tool}} | {{agent.plaza_dark.r1.s1.why}} | {{agent.plaza_dark.r1.s1.result}} | {{agent.plaza_dark.r1.s1.ms}} |
| 3 | {{agent.plaza_dark.r1.s2.tool}} | {{agent.plaza_dark.r1.reason}} | the alert, graded {{agent.plaza_dark.r1.severity}}: "{{agent.plaza_dark.r1.summary}}" | {{agent.plaza_dark.r1.s2.ms}} |

On the dimmed pictures the model reached for a closer look (`crop_zoom`, or CLAHE through `enhance`) before deciding,
which it did not do in the runs on the pictures as filmed. Two honest notes from these runs. In the as filmed runs the model
checked motion with `flow`, which returned "{{agent.plaza.r0.s1.result}}" for a person who was walking: the tool
measures the flow inside the track's last box between two frames that can be seconds apart, so it missed the motion,
and the model decided from the frames. That is a defect in `agent/tools.py`, open for the fix pass. And these are four
checks of one moment, a demonstration, not an evaluation of the agent's accuracy (section 11).

**Task success.** On the Plaza clip the rules raised {{eval.plaza.fps_2.ai.candidates}} candidates at
{{say:2 frames a second}}, which became {{eval.plaza.fps_2.ai.incidents}} incidents and
{{eval.plaza.fps_2.ai.checks}} AI checks: grouping keeps the AI to one check per situation. That run used a stand in
that calls every moment real, so it counts checks, not the agent's accuracy.

**Failure handling.** A reply that is not valid JSON for the schema gets one more try, then the check ends. When steps
or time run out, or on an AI error, timeout or daily cap, a critical candidate becomes an unverified alert with
OpenCV's reasons ("Not checked by the AI"), and anything less is dismissed as not verified. An incident held for its
check is posted unverified after {{say:15 s}}. A guard's False alarm quiets only that very trigger for
{{say:5 minutes}}; an AI alert verdict is never silenced by an earlier tap.

**Observability.** The incident timeline shows every step, who acknowledged or marked a false alarm, when, and
whether from the app or Telegram. Taps are labels: `precision_by_watch` counts false alarms per watch. On AWS,
CloudWatch counts AI calls and alerts.

**Security and limits.** The AI sees only the flagged moment's pictures, from the camera's own society (a picture of
another society cannot reach a check). Daily caps: {{plans.free.ai_checks}} checks for a free society and
{{plans.society.ai_checks}} per watched camera on the paid plan, counted once per check however many steps; a separate
cap for writing watches and questions; demo sandboxes have smaller caps of their own and a total for the whole demo.

**Human control.** `ask_human` leaves the incident waiting for a person. Anyone in the society's linked Telegram group
can acknowledge or mark a false alarm (guards are often not app members), and supervisors can in the app. Alerts
always say "Possible"; a person decides what to do.

**The age rule.** The verifier never estimates or states anyone's age. A question that needs an age ends with
`cannot_judge`, which posts the moment unchecked for a person to see; any age words the model writes in a summary are
replaced ("A child of about 7 is in the pool" becomes "Someone is in the pool"), while place names such as "kids play
area" are kept.

## 8. Evaluation

### 8.1 Method

We have one labelled clip today: OpenCV's sample `vtest.avi`, a busy plaza seen from above
({{eval.plaza.duration_s}} long), the same scene the Plaza demo camera plays. It is not a society camera. One of us
labelled it by eye from its frames, with no second check: every time a person's feet came into the left walkway zone
(`enter`, {{eval.plaza.truth.enter}} events in the scored part), and every crossing of the walkway's edge line going
onto it (`cross_in`, {{eval.plaza.truth.cross_in}}) or off it (`cross_out`, {{eval.plaza.truth.cross_out}}). The
rules for each label are in `eval/results/eval-plaza.json`, the labels in `eval/labels/plaza.csv`.

`python eval/evaluate.py eval/labels/plaza.csv --fps 10,2,1` plays the clip through a camera process's own code (every
due frame analysed) at {{say:10, 2 and 1 analysed frames a second}}, and every candidate through the app's own path
(dedupe, incidents, the AI check, here a stand in that calls every moment real and counts the checks). A candidate
matches a label of its watch within {{eval.plaza.tolerance_s}}, one to one; the first and last seconds are not scored
({{eval.plaza.scored_s}} scored). Precision and recall come with Wilson intervals at {{say:95%}} confidence, and the
rule of three when nothing failed. Scored on {{eval.plaza.cpu}} with OpenCV {{eval.plaza.opencv}} (COOL:
{{eval.plaza.cool}}) on {{eval.plaza.date}}, engine commit {{eval.plaza.engine}}.

### 8.2 Results on the Plaza clip

| Watch, rate | Labelled | Matched | False alarms | Missed | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- |
| Enters the left walkway, {{say:10 fps}} | {{eval.plaza.fps_10.enter.truth}} | {{eval.plaza.fps_10.enter.tp}} | {{eval.plaza.fps_10.enter.fp}} | {{eval.plaza.fps_10.enter.fn}} | {{eval.plaza.fps_10.enter.precision}} ({{eval.plaza.fps_10.enter.precision_ci}}) | {{eval.plaza.fps_10.enter.recall}} ({{eval.plaza.fps_10.enter.recall_ci}}) |
| Enters, {{say:2 fps}} | {{eval.plaza.fps_2.enter.truth}} | {{eval.plaza.fps_2.enter.tp}} | {{eval.plaza.fps_2.enter.fp}} | {{eval.plaza.fps_2.enter.fn}} | {{eval.plaza.fps_2.enter.precision}} ({{eval.plaza.fps_2.enter.precision_ci}}) | {{eval.plaza.fps_2.enter.recall}} ({{eval.plaza.fps_2.enter.recall_ci}}) |
| Enters, {{say:1 fps}} | {{eval.plaza.fps_1.enter.truth}} | {{eval.plaza.fps_1.enter.tp}} | {{eval.plaza.fps_1.enter.fp}} | {{eval.plaza.fps_1.enter.fn}} | {{eval.plaza.fps_1.enter.precision}} ({{eval.plaza.fps_1.enter.precision_ci}}) | {{eval.plaza.fps_1.enter.recall}} ({{eval.plaza.fps_1.enter.recall_ci}}) |
| Crosses the edge going in, {{say:10 fps}} | {{eval.plaza.fps_10.cross_in.truth}} | {{eval.plaza.fps_10.cross_in.tp}} | {{eval.plaza.fps_10.cross_in.fp}} | {{eval.plaza.fps_10.cross_in.fn}} | {{eval.plaza.fps_10.cross_in.precision}} ({{eval.plaza.fps_10.cross_in.precision_ci}}) | {{eval.plaza.fps_10.cross_in.recall}} ({{eval.plaza.fps_10.cross_in.recall_ci}}) |
| Going in, {{say:2 fps}} | {{eval.plaza.fps_2.cross_in.truth}} | {{eval.plaza.fps_2.cross_in.tp}} | {{eval.plaza.fps_2.cross_in.fp}} | {{eval.plaza.fps_2.cross_in.fn}} | {{eval.plaza.fps_2.cross_in.precision}} ({{eval.plaza.fps_2.cross_in.precision_ci}}) | {{eval.plaza.fps_2.cross_in.recall}} ({{eval.plaza.fps_2.cross_in.recall_ci}}) |
| Going in, {{say:1 fps}} | {{eval.plaza.fps_1.cross_in.truth}} | {{eval.plaza.fps_1.cross_in.tp}} | {{eval.plaza.fps_1.cross_in.fp}} | {{eval.plaza.fps_1.cross_in.fn}} | {{eval.plaza.fps_1.cross_in.precision}} ({{eval.plaza.fps_1.cross_in.precision_ci}}) | {{eval.plaza.fps_1.cross_in.recall}} ({{eval.plaza.fps_1.cross_in.recall_ci}}) |
| Crosses the edge going out, {{say:10 fps}} | {{eval.plaza.fps_10.cross_out.truth}} | {{eval.plaza.fps_10.cross_out.tp}} | {{eval.plaza.fps_10.cross_out.fp}} | {{eval.plaza.fps_10.cross_out.fn}} | {{eval.plaza.fps_10.cross_out.precision}} ({{eval.plaza.fps_10.cross_out.precision_ci}}) | {{eval.plaza.fps_10.cross_out.recall}} ({{eval.plaza.fps_10.cross_out.recall_ci}}) |
| Going out, {{say:2 fps}} | {{eval.plaza.fps_2.cross_out.truth}} | {{eval.plaza.fps_2.cross_out.tp}} | {{eval.plaza.fps_2.cross_out.fp}} | {{eval.plaza.fps_2.cross_out.fn}} | {{eval.plaza.fps_2.cross_out.precision}} ({{eval.plaza.fps_2.cross_out.precision_ci}}) | {{eval.plaza.fps_2.cross_out.recall}} ({{eval.plaza.fps_2.cross_out.recall_ci}}) |
| Going out, {{say:1 fps}} | {{eval.plaza.fps_1.cross_out.truth}} | {{eval.plaza.fps_1.cross_out.tp}} | {{eval.plaza.fps_1.cross_out.fp}} | {{eval.plaza.fps_1.cross_out.fn}} | {{eval.plaza.fps_1.cross_out.precision}} ({{eval.plaza.fps_1.cross_out.precision_ci}}) | {{eval.plaza.fps_1.cross_out.recall}} ({{eval.plaza.fps_1.cross_out.recall_ci}}) |

| Every watch together | {{say:10 fps}} | {{say:2 fps}} | {{say:1 fps}} |
| --- | --- | --- | --- |
| Precision | {{eval.plaza.fps_10.all.precision}} ({{eval.plaza.fps_10.all.precision_ci}}) | {{eval.plaza.fps_2.all.precision}} ({{eval.plaza.fps_2.all.precision_ci}}) | {{eval.plaza.fps_1.all.precision}} ({{eval.plaza.fps_1.all.precision_ci}}) |
| Recall | {{eval.plaza.fps_10.all.recall}} ({{eval.plaza.fps_10.all.recall_ci}}) | {{eval.plaza.fps_2.all.recall}} ({{eval.plaza.fps_2.all.recall_ci}}) | {{eval.plaza.fps_1.all.recall}} ({{eval.plaza.fps_1.all.recall_ci}}) |
| Delay from the labelled moment, enters, median and 90th percentile | {{eval.plaza.fps_10.enter.delay_median}}, {{eval.plaza.fps_10.enter.delay_p90}} | {{eval.plaza.fps_2.enter.delay_median}}, {{eval.plaza.fps_2.enter.delay_p90}} | {{eval.plaza.fps_1.enter.delay_median}}, {{eval.plaza.fps_1.enter.delay_p90}} |
| Distinct person ids (fewer is steadier) | {{eval.plaza.fps_10.person_ids}} | {{eval.plaza.fps_2.person_ids}} | {{eval.plaza.fps_1.person_ids}} |
| Candidates, incidents, AI checks | {{eval.plaza.fps_10.ai.candidates}}, {{eval.plaza.fps_10.ai.incidents}}, {{eval.plaza.fps_10.ai.checks}} | {{eval.plaza.fps_2.ai.candidates}}, {{eval.plaza.fps_2.ai.incidents}}, {{eval.plaza.fps_2.ai.checks}} | {{eval.plaza.fps_1.ai.candidates}}, {{eval.plaza.fps_1.ai.incidents}}, {{eval.plaza.fps_1.ai.checks}} |

**Reading it.** On a crowded scene the watches find about three of four labelled events at every rate, and precision
falls only a little as the rate falls. The false alarms per camera hour look large ({{eval.plaza.fps_2.all.false_alarms_per_hour}}
for every watch together at {{say:2 fps}}) because this plaza has an event every few seconds; on a quieter society
camera the same share of mistakes would be far fewer per hour, but that is not measured. A plain motion alarm (a new
alert after {{say:10 s}} without motion) sent this many alerts on this clip: {{eval.plaza.fps_2.motion_alarm_alerts}}; the plaza is
never still, so it cannot tell any of these events apart. AI checks per camera hour on this clip were
{{eval.plaza.fps_2.ai.per_camera_hour}}: the clip is shorter than the incident grouping window, so every repeat
joined the first incident without a check, and a longer busy recording would need more.

### 8.3 A failure we found and fixed: tracking at low frame rates

Our first shared camera pool delivered about one analysed frame a second per camera on a loaded server, and at that
rate the two IoU passes lost people between frames: each walker became several ids, so "enters" fired again and again,
and line crossings, which need the same id on both sides of the line, nearly vanished. The third association pass
(section 4.1, step 6) fixed it. The same evaluation on the engine before the fix
(`eval/results/eval-plaza_before_fix.json`, engine {{eval.plaza_before_fix.engine}}):

| Plaza clip | {{say:2 fps}}, before | {{say:2 fps}}, after | {{say:1 fps}}, before | {{say:1 fps}}, after |
| --- | --- | --- | --- | --- |
| Distinct person ids | {{eval.plaza_before_fix.fps_2.person_ids}} | {{eval.plaza.fps_2.person_ids}} | {{eval.plaza_before_fix.fps_1.person_ids}} | {{eval.plaza.fps_1.person_ids}} |
| Enters, false alarms | {{eval.plaza_before_fix.fps_2.enter.fp}} | {{eval.plaza.fps_2.enter.fp}} | {{eval.plaza_before_fix.fps_1.enter.fp}} | {{eval.plaza.fps_1.enter.fp}} |
| Crosses going in, recall | {{eval.plaza_before_fix.fps_2.cross_in.recall}} | {{eval.plaza.fps_2.cross_in.recall}} | {{eval.plaza_before_fix.fps_1.cross_in.recall}} | {{eval.plaza.fps_1.cross_in.recall}} |
| Crosses going out, recall | {{eval.plaza_before_fix.fps_2.cross_out.recall}} | {{eval.plaza.fps_2.cross_out.recall}} | {{eval.plaza_before_fix.fps_1.cross_out.recall}} | {{eval.plaza.fps_1.cross_out.recall}} |
| Every watch, precision | {{eval.plaza_before_fix.fps_2.all.precision}} | {{eval.plaza.fps_2.all.precision}} | {{eval.plaza_before_fix.fps_1.all.precision}} | {{eval.plaza.fps_1.all.precision}} |
| Every watch, recall | {{eval.plaza_before_fix.fps_2.all.recall}} | {{eval.plaza.fps_2.all.recall}} | {{eval.plaza_before_fix.fps_1.all.recall}} | {{eval.plaza.fps_1.all.recall}} |

At {{say:10 fps}} the fix changed little: every watch's recall was {{eval.plaza_before_fix.fps_10.all.recall}}
before and {{eval.plaza.fps_10.all.recall}} after. `backend/tests/test_track_plaza.py` keeps it fixed: on the same
detections, crossings at {{say:1 fps}} must stay within {{say:15%}} of those at {{say:10 fps}}.

### 8.4 Plates on real photos

Run once on {{plates.commons.photos}} Wikimedia Commons photos (handheld daylight photos, not gate CCTV; two whole cars
cropped as a detector would, six plate close ups), at default settings:

| Measure | Result |
| --- | --- |
| Plates the grammar covers | {{plates.commons.plates}} |
| Read exactly | {{plates.commons.exact}} ({{plates.commons.exact_share}}, {{plates.commons.exact_ci}}) |
| Read wrong | {{plates.commons.wrong}} (a two line close up whose box cut the right edge; the wrong text fits the grammar) |
| Left unread | {{plates.commons.unread}} (yellow, angled and dim) |
| Outside the grammar, left unread as they should be | {{plates.commons.outside_grammar_unread}} of {{plates.commons.outside_grammar}} |
| Plate colour right, unknown, wrong | {{plates.commons.colour_right}}, {{plates.commons.colour_unknown}}, {{plates.commons.colour_wrong}} of {{plates.commons.colour_photos}} |

The photos were not kept, so this cannot be rerun, and later reader changes were not run on them. On a synthetic gate
(a real car photo moved over a drawn gate, a person standing on the line, real YOLOX, tracker, line, plate reader and
gate log), {{build.plates.gate_runs_paired}} of {{build.plates.gate_runs}} runs at {{say:1 and 2 frames a second}}
logged one in and one out paired by plate, at confidences {{build.plates.gate_conf_min}} to
{{build.plates.gate_conf_max}}. One crop through the plate path took about {{build.plates.crop_ms}} on the dev box
(`LPD-YuNet` {{build.plates.lpd_ms}}, `PP-OCRv5` {{build.plates.ocr_line_ms}} a line).

### 8.5 Speed, memory and capacity on the dev box

`python eval/bench.py` runs the live camera pool (the app's own WorkerPool and WorkerManager) on the Plaza clip with
{{bench.devbox_no_text.cameras}} cameras on {{bench.devbox_no_text.vcpus}} CPUs, as a c8g.large would have, with a
stand in AI. These are dev box numbers, not Graviton ones.

| Dev box, {{bench.devbox_no_text.cameras}} cameras on {{bench.devbox_no_text.vcpus}} CPUs | App defaults without a watch in words (index on) | Lean (index off) |
| --- | --- | --- |
| Analysed frames a second per camera, median | {{bench.devbox_no_text.fps_per_camera|bare}} | {{bench.devbox_lean.fps_per_camera|bare}} |
| Frame latency, median and 95th percentile | {{bench.devbox_no_text.latency_p50}}, {{bench.devbox_no_text.latency_p95}} | {{bench.devbox_lean.latency_p50}}, {{bench.devbox_lean.latency_p95}} |
| Memory of the app and everything it started, median and peak | {{bench.devbox_no_text.memory_total}}, {{bench.devbox_no_text.memory_peak}} | {{bench.devbox_lean.memory_total}}, {{bench.devbox_lean.memory_peak}} |
| Largest camera process | {{bench.devbox_no_text.memory_camera_process}} | {{bench.devbox_lean.memory_camera_process}} |
| Cameras this box gives {{say:2 frames a second}} | {{bench.devbox_no_text.cameras_at_2fps}} | {{bench.devbox_lean.cameras_at_2fps}} |

Measured while building, in a container shaped like a c8g.large ({{say:2 pinned CPUs, 4 GB; 8 Plaza cameras for 75 s}}; index
off, no watch in words, a stand in AI): the shared pool used {{build.pool.c8g_shape.pool_steady_mb_run1}} and
{{build.pool.c8g_shape.pool_steady_mb_run2}} in two runs where one process per camera used
{{build.pool.c8g_shape.old_steady_mb_run1}} and {{build.pool.c8g_shape.old_steady_mb_run2}}, with peaks of
{{build.pool.c8g_shape.pool_peak_mb}} against {{build.pool.c8g_shape.old_peak_mb}}. Batching frames of several cameras
through YOLOX was tried and dropped: {{build.pool.batching.batch4_ms}} a frame in a batch of four against
{{build.pool.batching.single_ms}} one at a time, and the stock export returned different boxes for a batch.

A watch described in words is the expensive case: SigLIP 2's image tower in every camera process that needs it, and
its text tower in the app while new texts are made. With the app defaults, two cameras with such a watch reached
{{build.pool.text_watch.two_cameras_mb}} at first start, over a c8g.large's budget, where eight cameras without one
used {{build.pool.text_watch.eight_cameras_no_text_mb}}.

### 8.6 Failure cases

The evaluation saves a picture of the first false alarm and the first miss of each watch at each rate, with the
tracker's boxes drawn, in [eval/results/failures](../eval/results/failures/). At {{say:2 fps}}:

| Watch | First false alarm | Its picture | First miss | Its picture |
| --- | --- | --- | --- | --- |
| Enters | {{eval.plaza.fps_2.enter.first_false_alarm_at}} | `plaza-2fps-enter-first-false-alarm.jpg` | {{eval.plaza.fps_2.enter.first_miss_at}} | `plaza-2fps-enter-first-miss.jpg` |
| Going in | {{eval.plaza.fps_2.cross_in.first_false_alarm_at}} | `plaza-2fps-cross_in-first-false-alarm.jpg` | {{eval.plaza.fps_2.cross_in.first_miss_at}} | `plaza-2fps-cross_in-first-miss.jpg` |
| Going out | {{eval.plaza.fps_2.cross_out.first_false_alarm_at}} | `plaza-2fps-cross_out-first-false-alarm.jpg` | {{eval.plaza.fps_2.cross_out.first_miss_at}} | `plaza-2fps-cross_out-first-miss.jpg` |

Pictures for {{say:10 fps}} and {{say:1 fps}}, and for the engine before the tracker fix, are beside them. Failure
modes we know of from building and testing (section 12 has more): people walking close together seen from above merge
into one box, so one of them is missed; at the very start of a clip people already near the line are picked up as
they first appear; a plate box that cuts characters can read wrong but valid; the age guard refuses "a person pushing
a stroller".

### 8.7 Other measurements from the build

Each value in `eval/results/build_measurements.json` says what it measured, on what machine, where it was reported and
when.

* **Still in moving water** (`still_s`, rendered water at several speeds): a still body's own movement stayed at or
  under {{build.signals.still_body_max}} body heights a second, swimmers {{build.signals.swimmer_min}} to
  {{build.signals.swimmer_max}}; one DIS look took about {{build.signals.dis_look_ms}} on the dev box. The threshold
  sits between them at {{say:0.04}}.
* **Zone state by example** (a gate, closed against open): {{build.signals.gate_same_day}} against
  {{build.signals.gate_other_day}} by day, {{build.signals.gate_same_night}} against
  {{build.signals.gate_other_night}} at night. Without the blur before CLAHE the closed gate matched its own example at
  only {{build.signals.gate_same_no_blur}}.
* **The age guard's meaning check**: refused {{build.age_guard.meaning_refused}} of {{build.age_guard.must_refuse}}
  child texts in many spellings and scripts on its own, and {{build.age_guard.must_pass_refused}} of
  {{build.age_guard.must_pass}} ordinary ones; of {{build.age_guard.held_out}} held out child texts it refused
  {{build.age_guard.held_out_refused}} alone. The words and patterns catch the rest in their realistic forms.
* **The demo**: the browser test from the landing page's Try the demo to an acknowledged incident and its Telegram
  message took {{build.demo.walk_s}}. Ten sandboxes open with nobody watching used {{build.demo.idle_10_mb}}; all ten
  watched at once {{build.demo.watched_10_mb}}, at {{build.demo.watched_10_fps_min|bare}} to
  {{build.demo.watched_10_fps_max}} per camera (analysis is not shared between sandboxes; section 11).

## 9. User experience and accessibility

* **Calm by design.** The control room is a camera wall and one feed. One accent colour, red, is kept for severity and
  cameras in trouble; no gradients or glows. Plain words in sentence case ("Someone in the pool after hours, Pool
  camera, 10:42 PM"); detail behind a tap.
* **Three steps to a camera.** How it connects (this device, a phone by QR, a recorder, the bridge, an address, an
  upload), its name and purpose, then keep the watches its purpose suggests.
* **A watch in one box.** "What should Pehra watch for?", ideas for the camera's purpose, and a card that says in
  sentences what it will and will not watch before anything is saved. A setup task such as "Draw a line called main
  gate" opens the zone editor with the name filled in.
* **Telegram where guards already are.** One message per incident, edited in place; two buttons; a reminder if nobody
  acknowledges; the app's incident page shows the same message.
* **Works on a phone.** Every screen is checked at 1280 and 390 px wide with no sideways scrolling.
* **Accessibility checks.** The browser tests run axe (WCAG 2.1 A and AA) on the app's screens and the public pages and
  fail on any serious or critical problem; they also fail on an em dash, an en dash or a hyphenated word in the page
  text. The landing page scored {{build.web.lighthouse_mobile_accessibility}} for accessibility and
  {{build.web.lighthouse_mobile_performance}} for performance in Lighthouse on mobile.
* **The judges' demo.** Try the demo gives a private sandbox society for a day and opens the control room with an
  open critical incident, its picture and its Telegram message; a judge can add their laptop or phone as a camera,
  type a watch and see it fire. Incidents made before the visitor arrived are labelled "Example".
* Not yet: Hindi screens; a dark theme.

## 10. Responsible use, security and privacy

* **Faces only for consenting adult staff.** Enrolment needs a consent tick whose words say the person is an adult and
  agreed; three to five photos become templates and the photos are never stored; deletion reaches every camera process
  at once. Residents and visitors are never identified, and there is no search by photo.
* **Never children.** No child is enrolled, identified or aged. A watch about age is refused in layers: a word list
  (English, and Hindi in Latin and Devanagari script), explicit age patterns ("7 year old", "under 12", "class 5
  student"), a SigLIP 2 meaning check on every look text, and a flag the compiler model must set. The verifier never
  states an age. Pool watches are about situations: someone in the water after hours, still in the water, the
  lifeguard chair empty.
* **Telegram gets stills, not video.** Faces of anyone not enrolled are blurred, and the head of every other person;
  when blurring cannot run, the alert goes as text. Clips stay behind sign in, by role, and every view is audited.
  There is no bulk download.
* **Retention.** Footage, events, incidents, the gate log and attendance are deleted after {{plans.free.days}} days
  on the free plan and {{plans.society.days}} on the paid one, per society.
* **Notices.** A printable notice for each camera zone with the purpose, retention and whom to ask; the device page
  shows its own notice before it sends anything. The privacy page says the society is the data fiduciary and Pehra its
  processor under India's [Digital Personal Data Protection Act, 2023](https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf)
  and its Rules of 2025, most duties of which apply from 13 May 2027
  ([KSK on the Act for RWAs](https://ksandk.com/data-protection-and-data-privacy/dpdp-act-rwa-compliance-guide/)).
* **Security.** Google sign in or single use operator links; HttpOnly, Secure, SameSite cookies and origin checks;
  roles per society; device tokens, sign in links and Telegram connect codes stored only as hashes, and invites as
  signed single use links; camera addresses sealed with Fernet; tokens filtered out of server logs; rate limits; RTSP only, with private addresses refused and, on AWS, an
  egress rule behind it; secrets only in SSM or a local `.env`.
* **Honesty in the product.** Alerts say "Possible". Pehra does not claim drowning detection; the site calls it an
  aid, not a guarantee. The watch card says what cannot be watched. Demo incidents made before a visitor arrived are
  labelled examples and carry no AI verdict.
* **Cost safety.** The AI only for flagged moments, daily caps per society and for the demo, and a monthly AWS budget
  alarm.

## 11. What we did not do, and what we measured on what

* **Dev box, not Graviton.** Every speed, memory and capacity number in this report was measured on our x86 dev box (a
  2013 Xeon without AVX2, shared with other work) or in a container on it shaped like a c8g.large. None is from
  Graviton or COOL. Section 6 says "not measured yet" until `eval/bench_aws.sh --yes-spend` runs, which waits on the
  owner's go ahead.
* **AWS not deployed.** The stack is tested with `cfn-lint`, moto and a fake `aws`, not on AWS: COOL's Python path,
  aarch64 builds of every pinned package, H.265 decoding in COOL's OpenCV, the CloudWatch agent's metrics, the Bedrock
  model id in Mumbai and the egress rules on the COOL image are unverified until the real deploy.
* **S3 Object Lock is on but unused.** The bucket and the role allow locked writes, but the app does not lock
  incident clips yet; clips are deleted on the plan's schedule like everything else.
* **Plaza labels made by eye.** The labels were made by one of us from the clip's frames, with no second labeller.
  The clip is a public plaza seen from above, not a society camera.
* **No real gate CCTV yet.** The plate reader is measured on {{plates.commons.photos}} handheld daylight photos and a
  synthetic gate; no night, infrared, headlight glare or two wheeler footage. Plate colour under sodium light is not
  handled beyond darkness.
* **No pool or staff footage.** The pool signals (still in the water, alone, vanished) and the staff register are
  tested on rendered scenes and OpenCV's sample pictures, not on recordings. The owner's consented recordings are
  planned (`eval/record_checklist.md`).
* **The agent's accuracy is not measured.** The evaluation's AI check was a stand in that calls every moment real.
  The loop is tested with a scripted model, and the real model ran {{agent.plaza.runs}} checks as filmed and
  {{agent.plaza_dark.runs}} dimmed on one moment (section 7); no scored set of real verdicts exists. Those runs showed
  that the agent's `flow` tool measures no motion for a walking person (it looks inside the track's last box), which
  is open for the fix pass.
* **Polar unverified end to end.** No sandbox product exists and Polar's API was never called; the webhook fixture is
  built from the SDK's types, not a captured delivery.
* **Telegram unverified with a real bot.** Every Telegram path is tested against a mocked Bot API.
* **The age guard's limits.** The meaning check refuses "a person pushing a stroller" (and, on the default model, "a
  person with a school bag"). Bare fragments such as "of about eight" and "an under 14" pass every layer; their
  realistic forms ("a person of about eight") are refused. SigLIP's text tower does not read numbers or heights; the
  word patterns do. Look back search and questions about footage have no age guard yet. A lookout that follows one
  person by appearance for a day could follow a child; no check can tell without judging age.
* **The demo's sharing per clip was not done.** Decoding a demo clip is shared, but each sandbox's camera analyses the
  same frames again: many judges watching at once each get fewer frames a second (section 8.7). They run on the demo's
  own camera process, so a society is never slowed.
* **Watches described in words are costly** (section 8.5): SigLIP's towers are the largest models, and on a c8g.large
  a watch in words on several cameras needs care.
* **Not tried on real phones or recorders.** The device page is tested in desktop Chromium with a fake camera, not on
  a real phone or iOS Safari; the bridge was never run against a real Hikvision or CP Plus recorder, and its
  Dockerfile was never built.
* **Open review items** we know of: attendance is first and last sighting, not a crossing direction; "possible" face
  matches are not passed to the agent; a vehicle leaving through a gate without plate reading stays inside until its
  plate is seen again, so a yellow plate can get a false overstay; two overstays at one gate within ten minutes fold
  into one incident naming the first; Telegram stills refresh the enrolled faces every minute, so a deleted person's
  face may stay sharp in a still for up to a minute; a deployment wide AI cap is not built (the caps per society and
  for the demo are).

## 12. Limitations

* **One labelled clip.** The numbers in section 8 come from one busy plaza: a small sample of one scene, with wide
  intervals.
* **Thresholds from rendered scenes.** Still in water, zone state margins, the parked match and the plate thresholds
  were set on rendered or sample pictures during the build.
* **Crowds from above.** People who walk close together merge into one detection; at low frame rates two people of the
  same size in similar clothes passing more than a second apart can swap ids, and two cars of one size in the same
  lane can be joined.
* **Plates.** `LPD-YuNet` was trained on Chinese plates and its box moves with the input size; a box that cuts
  characters can give a wrong but valid read from a single crop; older plates with fewer than four number digits, and
  army, diplomatic and temporary plates, are left unread on purpose.
* **Faces.** On the device page, faces smaller than about 30 px in a 960 px frame are missed by the 320 px detector;
  a still from above, in profile or under infrared light will often show a pixelated person rather than a sharp
  scene.
* **Backtests.** Signals that need pixels (still, parked, zone state, looks) cannot be replayed from the event index;
  the card says "assuming ... held" and gives an approximate count.
* **Health.** A camera that has never sent a picture shows "Never connected" but raises no alert.
* **One server.** One instance and one app worker; some limiters and the push hub live in process memory. No scaling
  across servers yet; Postgres is planned and untested.

## 13. Calls we made

* General signals, not features per watch type; a purpose only suggests texts.
* The AI never sees the stream; critical alerts post before the AI check and are edited with its verdict.
* Camera health as a signal, and a default watch for every society.
* Telegram for alerts, because guards already use it; stills, never clips.
* Faces only for consenting adult staff; no age estimates; no search by photo; age words refused in layers.
* Pull cameras by RTSP only; the bridge and the device page for everything behind a router.
* One camera process per CPU sharing its models, instead of one per camera, to fit a society on one Graviton
  instance.
* A third tracker pass by distance, size and clothes, because a shared server delivers a frame or two a second.
* Billing per watched camera with Polar's unit based pricing; a society picks the cameras that matter.
* COOL proven by the file `cv2` is loaded from, not by KleidiCV in the build.
* Demo sandboxes with an allow list of what a visitor may do, on their own camera process, with their own AI caps.

## 14. Reproduce

The [README](../README.md) has the pinned dependencies and the commands to build, run, test and deploy. In short: build
the dev image (`deploy/Dockerfile.dev`), fetch the models (`python scripts/fetch_assets.py`), set `SESSION_SECRET` and
`DATA_KEY`, build the web app, start uvicorn, run `pytest`. The evaluation is
`python eval/evaluate.py eval/labels/plaza.csv --fps 10,2,1`, the benchmark `python eval/bench.py`, and the numbers in
this report come from `python3 docs/fill.py`.

## Sources

* Unwatched screens: [Studio Matrx control room guide](https://www.studiomatrx.org/guides/cctv-control-room-monitoring-india); [Ambient white paper, citing Sulman 2008](https://info.ambient.ai/hubfs/White%20Papers/White-Paper-TheHumanCeiling.pdf)
* Dead cameras: [The Tribune, Kharar](https://www.tribuneindia.com/news/chandigarh/residents-flag-rise-in-thefts-at-kharar-society/amp); [ETV Bharat, Bhopal](https://www.etvbharat.com/en/state/man-falls-into-lift-shaft-in-bhopal-body-found-after-10-days-enn26012006311)
* The gate: [ThePrint](https://theprint.in/india/security-guards-vs-delivery-agents-its-a-new-source-of-tension-across-delhi-ncr-societies/3049348/); [MyGate help](https://help.mygate.in/articles/134062-why-are-the-visitors-still-coming-to-my-flat-without-my-approval); [MyGate daily help](https://mygate.com/daily-help-management/)
* Pools: [The Tribune, Gurugram](https://www.tribuneindia.com/news/delhi/5-year-old-boy-drowns-in-gurugram-society-pool-643276); [Deccan Herald, Bengaluru](https://www.deccanherald.com/india/karnataka/bengaluru/seven-year-old-girl-drowns-in-swimming-pool-in-bengaluru-apartment-3062765)
* Guard costs: IndiaMART listings by [Kavitha](https://m.indiamart.com/kavithaenterpriseskarnataka/security-services.html), [Royal Guardforce](https://m.indiamart.com/royalguardforcesecurityservice) and [Adecco](https://m.indiamart.com/adeccostaffingsolutionsindia/security-services.html); [Studio Matrx guard guide](https://www.studiomatrx.org/guides/hiring-and-managing-society-guards-india)
* DPDP Act 2023: [the Gazette of India, from MeitY](https://www.meity.gov.in/static/uploads/2024/06/2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf); for RWAs: [KSK](https://ksandk.com/data-protection-and-data-privacy/dpdp-act-rwa-compliance-guide/)
* COOL: [AWS Marketplace listing](https://aws.amazon.com/marketplace/pp/prodview-fdvbfiewzuehs); [OpenCV's COOL page](https://opencv.org/cool/)
* EC2 prices: [Amazon EC2 on demand pricing](https://aws.amazon.com/ec2/pricing/on-demand/)
