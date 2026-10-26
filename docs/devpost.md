<!-- Made by docs/fill.py from docs/src/devpost.md. Edit that file, then run python3 docs/fill.py. -->

# Devpost submission: Pehra

Copy each field into the Devpost form. The form's own questions for this competition may differ from the standard
fields below: check the live form, and answer its questions from this page and the report. Every measured number links
to its file; a number that reads "not measured yet" must not be pasted until it is measured. Move an AWS sentence to
the past tense only if the AWS deploy has run; otherwise leave it as written (it says what is built and waiting).

## Project name

Pehra

## Elevator pitch (200 characters at most)

An AI control room for Indian housing societies: OpenCV 5 watches the CCTV they already have, an agent checks each
flagged moment with OpenCV tools, and guards get graded Telegram alerts.

## Thumbnail

The control room at desktop width: the camera wall with one tile outlined in red and the open critical incident at the
top of the feed (3:2, JPG or PNG under 5 MB). Only the demo's sample clip or consenting people may be visible.

## Try it out

* Live demo: https://pehra.dijmo.com (press Try the demo)
* Code: https://github.com/inventionsbyhamid/pehra
* Technical report: `docs/report.md` in the code repository

## Video demo link

not set yet

## Special awards

* **Agentic Vision:** yes. OpenCV flags a moment; a verifier agent calls OpenCV tools (zoom, CLAHE, denoise, DIS
  optical flow, pose history, the path walked) and its next step depends on what they return; it grades the alert,
  dismisses it, or asks a person; guards close the loop on Telegram. In a real trace on a dimmed frame the model
  brightened it with CLAHE before deciding, which it did not do on the frame as filmed. Report section 7 and
  `docs/agent-workflow.svg`.
* **Best Use of COOL:** the AWS deployment runs the whole pipeline in COOL's Python on Graviton, and the deploy fails
  unless `cv2` is loaded from under `/opt/cool`. The benchmark against stock OpenCV and x86 is built; it has run only
  on our x86 dev box so far. Report section 6 says "not measured yet" until it runs on AWS.

## Built with

```
opencv, opencv-5, opencv-js, cool, aws, amazon-ec2, aws-graviton, amazon-s3, amazon-bedrock, amazon-cloudwatch,
amazon-sns, aws-cloudformation, aws-systems-manager, claude, openrouter, deepseek, python, fastapi, uvicorn,
websockets, sqlite, sqlalchemy, alembic, litestream, numpy, yolox, mediapipe, yunet, sface, lpd-yunet, pp-ocr,
siglip-2, onnx, react, typescript, vite, telegram-bot-api, polar, google-oauth, cloudflare, caddy, ffmpeg,
playwright, axe-core, pytest, docker
```

## About the project

### What is live today, and what waits

* **Live today on our test server** (https://pehra.dijmo.com): the public site, Try the demo with a private sandbox society per
  visitor, the control room, camera pages, the watch editor with its coverage card and backtest, incidents with the
  exact Telegram message, the gate log and staff register, adding a laptop or phone as a camera, settings and the
  billing panel. It is one x86 container with stock OpenCV 5.0.0.93; files stay on its disk, and the AI runs on
  DeepSeek V4.1 Flash through OpenRouter. The only demo camera today is OpenCV's sample clip of a plaza.
* **Built and tested, waiting on the owner:** the AWS deployment (EC2 c8g.xlarge from the COOL image, S3, Bedrock,
  CloudWatch, SSM, one CloudFormation stack in Mumbai), a Telegram bot token, Google sign in keys, Polar products, the
  `pehra.dijmo.com` tunnel, and consented recordings of a pool, a main gate and a service gate.
* **Planned:** the COOL benchmark on AWS (`eval/bench_aws.sh --yes-spend`).

### Inspiration

A housing society in India runs dozens of cameras, sometimes hundreds, and nobody watches them. At best a busy guard
glances at one monitor, and footage is pulled out after something goes wrong
([Studio Matrx](https://www.studiomatrx.org/guides/cctv-control-room-monitoring-india)). Cameras die and nobody
notices: in one Kharar tower about 150 of 350 worked
([The Tribune](https://www.tribuneindia.com/news/chandigarh/residents-flag-rise-in-thefts-at-kharar-society/amp)).
Gate apps log what guards type, not what cameras see. And children have drowned in society pools left unattended
([The Tribune](https://www.tribuneindia.com/news/delhi/5-year-old-boy-drowns-in-gurugram-society-pool-643276);
[Deccan Herald](https://www.deccanherald.com/india/karnataka/bengaluru/seven-year-old-girl-drowns-in-swimming-pool-in-bengaluru-apartment-3062765)).
The cameras are already on the walls. We wanted them to speak up when something matters, in the guards' own Telegram
group, without anyone watching a wall of screens.

### What it does

* **A calm control room:** a camera wall with a health dot each, and one alert feed.
* **Any watch in plain words.** "Someone in the water after pool hours", "gate left open after 11 PM", "log every
  cab". Pehra compiles the words into general OpenCV signals and shows a card before anything runs: what it will
  watch and how reliably, what it cannot watch and the nearest thing it can, the setup it needs, and how often it would
  have fired in the last 24 hours.
* **Graded alerts on Telegram.** A critical alert goes to the society's group at once, before any AI check, as one
  message per incident, edited as things change, with Acknowledge and False alarm. Stills only, with the faces of
  anyone not enrolled blurred; the clip stays behind sign in.
* **An agent that checks before it bothers anyone,** with OpenCV tools (section below).
* **Gate log** of every vehicle with plate text and colour (yellow plates are commercial, so cabs), paired in and out;
  **staff register** of consenting adult staff from their faces; **camera health** for offline, covered, turned or
  blurred cameras.
* **Plug in what exists:** RTSP from Hikvision, CP Plus and Dahua recorders, a small bridge for recorders behind the
  society's router, any laptop or phone as a camera by link or QR code, and uploaded recordings.
* **Pricing:** [₹699](https://github.com/inventionsbyhamid/pehra/blob/main/backend/watchful/data/plans.json "plans.society.price_month_inr") per watched camera a month (about [$7](https://github.com/inventionsbyhamid/pehra/blob/main/backend/watchful/data/plans.json "plans.society.price_month_inr in plans.json over usd_inr.rate in plans.json")), with a free plan
  of [2](https://github.com/inventionsbyhamid/pehra/blob/main/backend/watchful/data/plans.json "plans.free.limits.watched_cameras") cameras; prices exclude GST and are charged in rupees through Polar.

### How we built it

Every pixel step runs on OpenCV 5, except SigLIP 2's forward pass, which runs on ONNX Runtime by default to fit in
memory (its preprocessing is OpenCV, and OpenCV DNN is one setting away):

* **Per frame:** `VideoCapture` with FFmpeg for RTSP, MOG2 to skip still frames, YOLOX through `cv2.dnn`, a
  `KalmanFilter` tracker with a third association pass by distance, size and an HSV clothing histogram, zones by
  `pointPolygonTest`, directed lines, DIS optical flow for "still in the water" compared with the water around the
  body, MediaPipe pose, YuNet and SFace for consenting staff, and camera health by ORB, a `USAC_MAGSAC` homography and
  the Laplacian.
* **Watches for any event:** general signals (crossed a line, stayed still, a zone's state learned from two saved
  pictures by CLAHE and Sobel edges plus SigLIP 2, a look described in words through SigLIP 2) that a
  compiler combines, checked in code, with a backtest over the last 24 hours.
* **Plates:** `LPD-YuNet` from the OpenCV Model Zoo, `warpPerspective`, `PP-OCRv5` recognition, an Indian plate
  grammar, voting across frames, and plate colour from HSV on the plate's background.
* **The agent:** OpenCV raises a candidate with its evidence; the verifier model replies with one tool call per step
  and gets each OpenCV result back before its next step; it grades, dismisses or asks a person. Critical alerts post
  first and are edited with the verdict.
* **Privacy in pixels:** Telegram stills blur every face that is not that person's own enrolled face (YuNet, SFace,
  pose for the head); a phone used as a camera blurs faces with OpenCV.js before sending.
* **Scale:** one camera process per vCPU sharing its models, every row, picture and face index scoped by society.
* **AWS (built, waiting on the owner's go ahead):** one CloudFormation stack in Mumbai: EC2 c8g.xlarge from the COOL
  image, S3, Bedrock, CloudWatch logs, metrics, dashboard and alarms, SSM for secrets, an egress rule against camera
  redirects, and a one command deploy with no SSH that fails unless COOL is the OpenCV loaded.

### Challenges we ran into

* **Tracking at the frame rate a shared server delivers.** Our first shared camera pool gave each camera about one
  analysed frame a second, and IoU tracking fell apart: at 1 fps line crossings going in dropped to
  [0%](https://github.com/inventionsbyhamid/pehra/blob/main/eval/results/eval-plaza_before_fix.json "runs.fps_1.watches.cross_in.recall.value") recall on the Plaza clip. A third association pass by distance over
  time, size and clothes brought it to [83%](https://github.com/inventionsbyhamid/pehra/blob/main/eval/results/eval-plaza.json "runs.fps_1.watches.cross_in.recall.value").
* **Plates that lie.** A plate box that cuts characters can still read as a valid plate. We fold the grammar cost, the
  box score and agreement across frames into the confidence; in [20,000](https://github.com/inventionsbyhamid/pehra/blob/main/eval/results/build_measurements.json "plates.fuzzed") fuzzed cut plates no wrong
  read reached the confidence needed to keep a text.
* **Still in moving water.** Optical flow inside a body's box moves with the water. Comparing it with the flow in a
  ring around the box separated still bodies from swimmers on rendered water.
* **Never about children.** Word lists keep missing spellings and languages, so the age guard has layers: words in
  English and ten Indian languages (their scripts and Latin spellings), age and height patterns, a SigLIP 2 meaning
  check, and the compiler's own flag. It reads watches, look back search, questions and the AI's own summaries.
* **Camera addresses are an attack surface.** FFmpeg follows HTTP redirects, so pulled cameras are RTSP only and, on
  AWS, the app's user cannot reach private addresses at all.

### Accomplishments that we're proud of

* A request never seen before ("someone carrying a ladder near the terrace door after 8 PM") compiles into general
  OpenCV signals with no new code, and the card says honestly what it cannot watch.
* On a crowded clip at 2 fps, the three watches together reached [86%](https://github.com/inventionsbyhamid/pehra/blob/main/eval/results/eval-plaza.json "runs.fps_2.all.precision.value") precision
  and [73%](https://github.com/inventionsbyhamid/pehra/blob/main/eval/results/eval-plaza.json "runs.fps_2.all.recall.value") recall, from [43](https://github.com/inventionsbyhamid/pehra/blob/main/eval/results/eval-plaza.json "runs.fps_2.ai.candidates") candidates that became
  [3](https://github.com/inventionsbyhamid/pehra/blob/main/eval/results/eval-plaza.json "runs.fps_2.ai.checks") AI checks.
* Privacy built into the pixels, not a policy page: blurred stills, blur on the device before sending, consent for
  every face template, and no photos kept.
* A judge can press Try the demo and, in their own sandbox, add their phone by QR code, type a watch and see it fire.

### What we learned

* The AI should never watch the stream. OpenCV decides when a moment is worth a look, and the AI is far more useful
  looking at that moment with OpenCV tools than at raw frames.
* Measure at the operating point you will really run at; our tracker worked at 10 fps and broke at
  1 fps.
* Say what you cannot do: a card that says "can't watch: a child's age" and offers "someone alone at the pool" earns
  more trust than a watch that silently never fires.

### What's next

* The COOL benchmark on AWS Graviton and the AWS deployment.
* Consented recordings of a pool, gates and staff arrivals, by day and night, to measure each watch where it will run.
* A first pilot society, Hindi screens, and the Telegram bot live.
* Sharing analysis between viewers of one demo clip, and lighter text towers for watches described in words.
