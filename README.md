# Pehra

Pehra (पहरा, "the watch") is an AI control room for the CCTV cameras an Indian housing society already has.

<!-- try start -->
## Try it

* Live demo: https://pehra.dijmo.com. Press Try the demo and you get a private sandbox society for a day, no sign in. It opens
  the control room with the Plaza demo camera (OpenCV's sample clip) and an open critical incident, with the Telegram
  message the society's group would get. Add your laptop or phone as a camera from the demo bar, type a watch such as
  "someone in front of the camera", and see it fire.
* Video: not set yet
* Code: https://github.com/inventionsbyhamid/pehra

The live demo runs on our own server, with stock OpenCV and an OpenRouter model. The AWS stack below is built and
tested but not deployed yet.
<!-- try end -->

<p align="center">
  <img src="docs/overview.svg" width="100%" alt="Pehra at a glance: cameras feed ingest and OpenCV perception (detect, track, zones and lines, faces at entries, plates, camera health). Watches in plain words flag moments, an AI check looks at them, and incidents reach the control room and Telegram. Perception also fills the gate log and staff register. It runs on AWS (EC2 Graviton, S3, Bedrock, SSM, CloudWatch, SNS, CloudFormation) or self hosted with Docker.">
</p>

## What it does

Most societies record every camera all day and nobody watches. Pehra watches all of them live with OpenCV 5 and tells
the security team what matters.

Anyone on the committee can add a watch in plain words, like "someone in the water after pool hours" or "gate left
open after 11 PM". Before it is saved, Pehra shows what it will watch, what it cannot, and how often it would have
fired over the last day. When OpenCV flags a moment, an AI agent takes a closer look using OpenCV tools. If it is
real, the society's Telegram group gets an alert with a still picture, faces blurred, and Acknowledge and False alarm
buttons.

Pehra also keeps a gate log of vehicles by plate and a register of staff who agreed to be enrolled, and it raises an
alert when a camera goes offline, is covered, turned or blurred.

## How it works

1. Cameras come in over RTSP from recorders, through a small bridge program for recorders behind a router, from a
   phone or laptop browser, or as an uploaded video.
2. OpenCV finds people and vehicles, tracks them, and checks the zones and lines drawn on each camera. It also reads
   plates at gates, recognises enrolled staff at entries and watches each camera's health.
3. The AI turns a watch written in plain words, once, into simple signals. A rule engine checks those signals on every
   analysed frame.
4. When a rule fires, the AI agent looks at a few stills of that moment, never the stream, and decides.
5. The incident shows up in the control room and, if it matters, on Telegram.

Each replaceable part (detector, AI, storage, alerts) is picked by a setting. The [detailed
architecture](docs/architecture.svg) and the [agent workflow](docs/agent-workflow.svg) show the full picture.

## How OpenCV is used

| What | OpenCV | Why |
| --- | --- | --- |
| Reading cameras | `VideoCapture` with FFmpeg | RTSP streams, uploads and the demo clip |
| Skipping still scenes | `createBackgroundSubtractorMOG2` | The detector runs only when something moves |
| Night | `createCLAHE` | Dark frames are brightened first |
| People and vehicles | YOLOX nano on `cv2.dnn` | Every analysed frame |
| Tracking | `KalmanFilter` | Steady ids for time in a zone and line crossings |
| Zones and lines | `pointPolygonTest` | Inside, entering, crossing in a direction |
| Falls | MediaPipe pose on `cv2.dnn` | Torso angle over time |
| Still in the water | `DISOpticalFlow` | A body that stops moving while the water moves |
| Faces | `FaceDetectorYN` (YuNet), `FaceRecognizerSF` (SFace) | Blur everyone; recognise consenting staff at entries |
| Plates | `LPD-YuNet` and PP-OCR on `cv2.dnn`, `warpPerspective` | The gate log |
| Camera health | ORB, `findHomography` with `USAC_MAGSAC`, `Laplacian` | Turned, blurred or covered cameras |
| Gate open or closed | `Sobel`, `matchTemplate` | A zone compared with example pictures |
| The agent's tools | zoom, `createCLAHE`, `fastNlMeansDenoisingColored`, DIS flow | A closer look at a flagged moment |
| A phone as a camera | OpenCV.js `FaceDetectorYN` | Faces blurred before a picture leaves the phone |

One model is not run by OpenCV by default. SigLIP 2, which matches looks described in words, runs on ONNX Runtime
because it needs far less memory there; OpenCV still prepares every crop, and `EMBEDDER_RUNTIME=opencv` moves it to
`cv2.dnn`.

## The AI check

The AI never watches video. It has three jobs: turn a watch written in plain words into signals OpenCV can check;
look at a flagged moment and decide; and answer questions about what happened.

To check a moment, the agent can ask for more frames, zoom in, brighten or denoise, measure optical flow inside the
box, read the person's pose over time, or draw the path they walked. Each of those is an OpenCV call. It then raises
the alert with a grade, dismisses it, asks a person, or says it cannot judge, which is what it must say about anyone's
age.

Some things it never decides. A critical alert goes to Telegram at once, before the check. If the AI is down or the
day's limit is used up, the moment is still filed, marked as not checked. Camera health and staff faces are measured by
OpenCV alone. On AWS the model is Claude Haiku 5.5 on Amazon Bedrock; the live demo uses DeepSeek V4.1 Flash through
OpenRouter.

<!-- aws start -->
## AWS

We built the production deployment for AWS in Mumbai (`ap-south-1`): one CloudFormation stack around one Graviton
instance. It has not run on AWS yet.

| Service | What it does for Pehra |
| --- | --- |
| EC2 c8g (Graviton4), from the COOL image | Runs everything, with OpenCV 5 built for Graviton |
| S3 | Pictures, clips and a Litestream copy of the database |
| Bedrock | Claude Haiku 5.5, for the AI check, writing watches and answering questions |
| SSM Parameter Store | Settings and secrets |
| CloudWatch | Logs, metrics, a dashboard and alarms |
| SNS | Alarm emails |
| CloudFormation | The whole stack, with a budget alarm |

To deploy, subscribe to COOL for Graviton4 in AWS Marketplace, turn on Claude Haiku 5.5 in Bedrock (the account
needs the Paid plan for both), then:

```
cp deploy/params.env.example deploy/params.env && chmod 600 deploy/params.env   # COOL image id, alert email, keys
deploy/deploy.sh --dry-run
deploy/deploy.sh
```

Point your domain at the address it prints. The deploy refuses to finish unless OpenCV from COOL is the one running.
<!-- aws end -->

<!-- selfhost start -->
## Self hosting

Pehra runs on any 64 bit Linux machine with Docker, in the society's office or in a cloud. One container holds the
whole app and one volume holds its data. The image is [4,254 MB](eval/results/build_measurements.json "selfhost.image_mb"), most of it models. For a sense of
memory and CPU: our old dev box (a 2013 Xeon) ran [8](eval/results/bench-devbox_6cpu_no_text.json "scenario.cameras") busy cameras on
[6](eval/results/bench-devbox_6cpu_no_text.json "scenario.vcpus") CPUs in [3,710 MB](eval/results/bench-devbox_6cpu_no_text.json "memory_mb.total_median"), analysing
[2.1 frames a second](eval/results/bench-devbox_6cpu_no_text.json "fps.per_camera.median") per camera.

```
git clone https://github.com/inventionsbyhamid/pehra.git
cd pehra
cp .env.example .env        # then fill in the settings below
docker compose up -d --build
```

The first build downloads every model and checks it by SHA 256. These are the settings you set in `.env`; every other
one is explained in `.env.example`.

| Setting | What to put there |
| --- | --- |
| `SESSION_SECRET`, `DATA_KEY` | Two random strings: `python3 -c "import secrets; print(secrets.token_hex(32))"`, once each. Keep `DATA_KEY` safe: camera addresses sealed with a lost key cannot be read. |
| `PUBLIC_BASE_URL` | The https address people will open. |
| `OPENROUTER_API_KEY` | For the AI. Or `AI_PROVIDER=bedrock` with `BEDROCK_MODEL` and AWS credentials. Without either, critical moments still alert, marked unverified. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | Your bot's token from BotFather, and one more random string. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional Google sign in. |
| `ALLOW_PRIVATE_STREAMS` | `true` only when the machine sits inside the society's network. |

The app listens on port 8000 of the machine itself. For https, any reverse proxy or a Cloudflare Tunnel works.

Sign in the first time with a link printed on the server. It creates the society and makes you its owner:

```
docker compose exec pehra python -m watchful.auth signin-link you@example.com --workspace "Green Park"
```

Then add cameras from the control room. To connect Telegram, open Settings, Telegram, Connect a group, add your bot
to the group and send it the `/connect` code shown there. Backups and upgrades are on the
[self hosting page](https://pehra.dijmo.com/docs/self-hosting) of the website docs.
<!-- selfhost end -->

## Privacy and safety

Pehra never enrols, identifies or estimates the age of children, and it refuses any watch about age. Faces are
recognised only for adult staff who agreed, and only on entry, gate and service door cameras. Everyone else is a
person, a car or a cab. Telegram gets still pictures with faces blurred, and a phone used as a camera blurs faces before
anything leaves it. Clips stay behind sign in, footage is deleted on a schedule, and nobody can search footage with a
photo of someone's face.

<!-- results start -->
## Results

We ran the live engine on OpenCV's Plaza sample clip, which we labelled by eye ourselves, at 2 analysed frames a
second. We also tested the plate reader on real photos and the camera pool on our x86 dev box. Each number links to
its file in [eval/results](eval/results). The Graviton and COOL numbers come with the AWS run.

| Plaza clip, 2 fps | Labelled | Matched | False alarms | Missed | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- |
| Enters the left walkway | [19](eval/results/eval-plaza.json "runs.fps_2.watches.enter.truth") | [14](eval/results/eval-plaza.json "runs.fps_2.watches.enter.tp") | [2](eval/results/eval-plaza.json "runs.fps_2.watches.enter.fp") | [5](eval/results/eval-plaza.json "runs.fps_2.watches.enter.fn") | [88%](eval/results/eval-plaza.json "runs.fps_2.watches.enter.precision.value") | [74%](eval/results/eval-plaza.json "runs.fps_2.watches.enter.recall.value") |
| Crosses the edge going in | [18](eval/results/eval-plaza.json "runs.fps_2.watches.cross_in.truth") | [13](eval/results/eval-plaza.json "runs.fps_2.watches.cross_in.tp") | [2](eval/results/eval-plaza.json "runs.fps_2.watches.cross_in.fp") | [5](eval/results/eval-plaza.json "runs.fps_2.watches.cross_in.fn") | [87%](eval/results/eval-plaza.json "runs.fps_2.watches.cross_in.precision.value") | [72%](eval/results/eval-plaza.json "runs.fps_2.watches.cross_in.recall.value") |
| Crosses the edge going out | [12](eval/results/eval-plaza.json "runs.fps_2.watches.cross_out.truth") | [9](eval/results/eval-plaza.json "runs.fps_2.watches.cross_out.tp") | [2](eval/results/eval-plaza.json "runs.fps_2.watches.cross_out.fp") | [3](eval/results/eval-plaza.json "runs.fps_2.watches.cross_out.fn") | [82%](eval/results/eval-plaza.json "runs.fps_2.watches.cross_out.precision.value") | [75%](eval/results/eval-plaza.json "runs.fps_2.watches.cross_out.recall.value") |

| Measure | Result |
| --- | --- |
| Every watch together at 2 fps, precision and recall | [86%](eval/results/eval-plaza.json "runs.fps_2.all.precision.value") ([72% to 93%](eval/results/eval-plaza.json "runs.fps_2.all.precision.wilson95")), [73%](eval/results/eval-plaza.json "runs.fps_2.all.recall.value") ([60% to 84%](eval/results/eval-plaza.json "runs.fps_2.all.recall.wilson95")) |
| Line crossings going in at 1 fps, recall before and after the tracker fix | [0%](eval/results/eval-plaza_before_fix.json "runs.fps_1.watches.cross_in.recall.value"), [83%](eval/results/eval-plaza.json "runs.fps_1.watches.cross_in.recall.value") |
| Plates read exactly, of real photos the grammar covers (not gate CCTV) | [5](eval/results/plates-commons.json "all.exact") of [7](eval/results/plates-commons.json "all.plates"); wrong [1](eval/results/plates-commons.json "all.wrong"), unread [1](eval/results/plates-commons.json "all.unread") |
| 8 busy cameras on 6 CPUs of our 2013 dev box: analysed frames a second per camera, without and with a watch described by looks | [2.1](eval/results/bench-devbox_6cpu_no_text.json "fps.per_camera.median"), [0.47](eval/results/bench-devbox_6cpu_text.json "fps.per_camera.median") |
| Memory of the whole app in those runs, median | [3,710 MB](eval/results/bench-devbox_6cpu_no_text.json "memory_mb.total_median"), [5,728 MB](eval/results/bench-devbox_6cpu_text.json "memory_mb.total_median") |

What goes wrong, so far: people walking close together, seen from above, merge into one box and one of them is
missed; at very low frame rates two similar people can swap ids; a plate box that cuts characters can give a wrong but
valid read; the age guard also refuses some harmless phrases, such as "a kids bicycle left on the ramp"; and a watch
described by looks costs the most on old CPUs, so on our dev box, which has no AVX2, it stays under one analysed frame
a second per camera. The first
false alarm and first miss of each watch are saved as pictures in
[eval/results/failures](eval/results/failures). One labelled clip is a small sample of one scene, and we have no
society footage labelled yet. The [report](docs/report.md) has the details.
<!-- results end -->

## Tests

In the dev container (`deploy/Dockerfile.dev`, after `python scripts/fetch_assets.py`):

```
PYTHONPATH=backend pytest -q                                   # the backend
cd web && npm ci && npm run build && npx playwright test       # the browser tests
cfn-lint deploy/cloudformation.yaml                            # the AWS template
python eval/evaluate.py eval/labels/plaza.csv --fps 10,2,1     # the evaluation above
python3 docs/fill.py --check --strict                          # every number in the documents has a source
```

## Models and licences

Every model allows a paid service. `scripts/fetch_assets.py` downloads each one and checks its SHA 256.

| Model | What it does here | Licence |
| --- | --- | --- |
| YOLOX nano (Megvii) | People and vehicles | Apache 2.0 |
| MediaPipe pose (OpenCV Model Zoo) | Falls, posture | Apache 2.0 |
| YuNet and SFace (OpenCV Model Zoo) | Faces to blur; consenting staff | MIT and Apache 2.0 |
| `LPD-YuNet` (OpenCV Model Zoo) | Plate boxes | Apache 2.0 |
| `PP-OCRv3` detection (OpenCV Model Zoo), `PP-OCRv5` recognition (PaddlePaddle) | Reading plates | Apache 2.0 |
| SigLIP 2 base patch32 (ONNX by `onnx-community`) | Looks described in words | Apache 2.0 |
| OpenCV.js 5.0 (`@techstark/opencv-js`) | Face blur on the phone | Apache 2.0 |

We do not use Ultralytics YOLO (AGPL). The sample clip `vtest.avi` comes from OpenCV's samples.

Python 3.12 and every package are pinned in `requirements.lock` (OpenCV is `opencv-python-headless` 5.0.0.93); the
web app's packages are pinned in `web/package-lock.json`.

## Documents

* [Technical report](docs/report.md)
* [Detailed architecture](docs/architecture.svg) and [agent workflow](docs/agent-workflow.svg)
* The bridge for recorders behind a router: [bridge/README.md](bridge/README.md)
* Website docs (getting started, privacy, self hosting): https://pehra.dijmo.com/docs

## Licence

MIT, see [LICENSE](LICENSE). The models keep their own licences, listed above.
