# Pehra

Every camera in your society, watched. Pehra (पहरा, "the watch") is an AI control room for the CCTV cameras an Indian
housing society already has. OpenCV 5 watches every camera live; anyone can add a watch in plain words and see
honestly what it will and will not watch; an agent checks each flagged moment with OpenCV tools; and the society's
Telegram group gets a graded alert with faces blurred, a gate log of every vehicle and a register of staff arrivals.

Built for the OpenCV AI Competition 2026 with OpenCV 5 and AWS by Hamid Mahmood Siddiqui. The full write up is in
[docs/report.md](docs/report.md), with the [architecture](docs/architecture.svg) and the
[agent workflow](docs/agent-workflow.svg).

<!-- try start -->
<!-- try end -->

## What it does

* **Control room:** a wall of camera tiles with a health dot each, and one alert feed.
* **Any watch in plain words:** "someone in the water after pool hours", "gate left open after 11 PM", "log every
  cab". Pehra compiles the words into general OpenCV signals and shows a card first: what it will watch and how
  reliably, what it cannot and the nearest alternative, the setup it needs, and how often it would have fired in the
  last 24 hours.
* **Graded alerts on Telegram:** critical alerts go out at once, before the AI check, as one message per incident,
  edited as things change, with Acknowledge and False alarm buttons. Stills only, with the faces of anyone not
  enrolled blurred.
* **A verifier agent with OpenCV tools:** zoom, brighten, optical flow, pose history and the path walked; it raises
  the alert with a grade, dismisses it, or asks a person. The AI never watches the stream.
* **Gate log** with plate text and colour, in and out paired by plate; **staff register** from consenting adult
  staff only; **camera health** for offline, covered, turned or blurred cameras.
* **Cameras in:** RTSP from Hikvision, CP Plus and Dahua recorders, the Pehra bridge for recorders behind a router,
  any laptop or phone as a camera (faces blurred in the browser by OpenCV.js before sending), and uploads.

<!-- pricing start -->
<!-- pricing end -->

## Run it on your computer

You need Docker and about 4 GB of free disk for the models.

```
docker build -f deploy/Dockerfile.dev -t pehra-dev .
docker run --rm -it -p 8000:8000 -v "$PWD":/app -w /app pehra-dev bash
```

Inside the container:

```
python scripts/fetch_assets.py      # every model and the sample clips, each checked by SHA 256
cp .env.example .env                # then set SESSION_SECRET and DATA_KEY, see below
(cd web && npm ci && npm run build)
PYTHONPATH=backend uvicorn watchful.api:create_app --factory --host 0.0.0.0 --port 8000
```

Make the two secrets for `.env` with `python -c "import secrets; print(secrets.token_hex(32))"`, once each.
`DATA_KEY` seals camera addresses; keep it, since addresses sealed with a lost key cannot be read.

Open http://localhost:8000 and press Try the demo for a private sandbox society with the Plaza demo camera (OpenCV's
`vtest.avi`). To sign in as yourself without Google keys, print a single use link on the server:

```
PYTHONPATH=backend python -m watchful.auth signin-link you@example.com --workspace "Green Park"
```

Browsers allow the camera on localhost; anywhere else the site needs https. Set `OPENROUTER_API_KEY` in `.env` for
the AI (writing watches, checking flagged moments, answering questions); without it a critical moment still alerts,
marked unverified. Telegram needs `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` and an https `PUBLIC_BASE_URL`;
then Settings, Telegram gives the code to send in the society's group. Every other setting, and what it does, is in
`.env.example`. The Python package is called `watchful`, after the project Pehra grew out of; the product name comes
from `brand.json`.

## Run it on our test server

The live demo runs on our own server (ioflood) in one Docker container, `watchful-app`, built from the same dev
image. `../wapp` (a helper outside this repository) copies the code there, builds the web app and recreates the
container. Visitors reach it through a Cloudflare tunnel (`watchful-tunnel`): a named tunnel on `pehra.dijmo.com`
once its token is on the server, a quick tunnel on a random address until then; `../wapp url` prints the current one,
and `docs/links.json` holds the one the documents show. The test server keeps files on its own disk, runs stock
OpenCV on x86, and calls the AI through OpenRouter.

Before deploying a new version, give the societies that need more than the free plan's cameras a comp, or the plan
check at start switches their newest cameras off:
`python -m watchful.billing comp <society id> society --cameras N --until 2026-11-30`.

## Deploy it to AWS

Everything runs in Mumbai (`ap-south-1`) on one Graviton instance (c8g.xlarge by default) from the COOL image, from
`deploy/cloudformation.yaml`. Not yet run on AWS: the stack is tested with `cfn-lint`, moto and a fake `aws`.

1. An AWS account on the Paid plan (the Free plan cannot launch c8g or Marketplace images), and the AWS CLI with a
   profile named `pehra`.
2. In AWS Marketplace, subscribe to COOL for Graviton4 (`prodview-fdvbfiewzuehs`) and copy its image id for Mumbai.
3. In the Bedrock console, turn on access to Claude Haiku 5.5.
4. For Google sign in, add `https://<your domain>/auth/google/callback` to the OAuth client. For Polar, point the
   webhook at `https://<your domain>/api/billing/webhook`. For Telegram, make a bot with BotFather.
5. `cp deploy/params.env.example deploy/params.env && chmod 600 deploy/params.env`, then fill in the COOL image id,
   an alert email and the keys you have. Leave `SESSION_SECRET`, `DATA_KEY` and `TELEGRAM_WEBHOOK_SECRET` blank to
   have them made once and kept.
6. Commit what should ship (only committed code is deployed), run `deploy/deploy.sh --dry-run`, read it, then run
   `deploy/deploy.sh`.
7. Point the domain's A record at the address it prints. `https://<your domain>/healthz` should answer.

The deploy checks itself: it fails unless `cv2` is loaded from COOL's install under `/opt/cool` with KleidiCV in the
build and decodes H.264 and H.265, and if the app uses more memory than its budget. It ends by writing
`eval/results/aws.json` with what it checked, for the documents. To run a command on the server later:
`deploy/deploy.sh --run '<command>'`.

## Tests

In the dev container:

```
PYTHONPATH=backend pytest -q              # the backend tests (slow ones are left out by default)
PYTHONPATH=backend pytest -q -m slow      # only the slow checks
cd web && npx playwright test             # browser tests against a running server (see web/e2e/signin.ts)
cfn-lint deploy/cloudformation.yaml
python3 docs/fill.py --check              # which numbers in the documents are still waiting
```

## Evaluation and benchmark

```
python eval/evaluate.py eval/labels/plaza.csv --fps 10,2,1   # the live engine on a labelled clip
python eval/bench.py --setup devbox --cameras 8 --cpus 2       # the live camera pool: frames a second, memory, cost
eval/bench_aws.sh                                              # the COOL benchmark plan and its cost; starts nothing
eval/bench_aws.sh --yes-spend                                  # runs it on temporary AWS instances
python3 docs/fill.py                                           # every measured number into the documents
```

How to record the society clips the evaluation is waiting for is in `eval/record_checklist.md`.

<!-- results start -->
<!-- results end -->

## Models and their licences

Every model allows a paid service. `scripts/fetch_assets.py` downloads them and checks each SHA 256 listed in
`backend/watchful/vision/models.py`.

| Model | What it does here | Licence |
| --- | --- | --- |
| YOLOX nano (Megvii) | People and vehicles | Apache 2.0 |
| MediaPipe pose (OpenCV Model Zoo) | Falls, hands, where a head is for blurring | Apache 2.0 |
| YuNet and SFace (OpenCV Model Zoo) | Faces to blur; templates of consenting staff | MIT and Apache 2.0 |
| `LPD-YuNet` (OpenCV Model Zoo) | Number plate boxes | Apache 2.0 |
| `PP-OCRv3` detection and CRNN (OpenCV Model Zoo), `PP-OCRv5` recognition (PaddlePaddle) | Reading plates | Apache 2.0 |
| SigLIP 2 base patch32 (ONNX by `onnx-community`), on ONNX Runtime (MIT) by default | Looks described in words, zone pictures, search | Apache 2.0 |
| OpenCV.js 5.0 (`@techstark/opencv-js`) | Face blur on the device page | Apache 2.0 |

Ultralytics YOLO (AGPL) is not used. The sample clip `vtest.avi` and the test pictures come from OpenCV's samples.

## Pinned dependencies

* Python 3.12. Every Python package is pinned in `requirements.lock` (from `requirements.in`), among them
  `opencv-python-headless` 5.0.0.93, FastAPI 0.161.1, uvicorn 0.54.0, NumPy 2.5.4, SQLAlchemy 2.1.6, Alembic 1.20.1,
  httpx 0.28.1, boto3 1.43.118, onnxruntime 1.23.2 and `polar-sdk` 1.0.2.
* Web packages are pinned in `web/package-lock.json`: React 18.3.1, React Router 6.30.6, Vite 7.3.7, TypeScript 5.9.3,
  `@techstark/opencv-js 5.0.0-release.1`, Playwright 1.64.1 and axe 4.14.0.
* The dev image (`deploy/Dockerfile.dev`) is Python 3.12 on Debian bookworm with Node 22 and FFmpeg.
* On AWS: OpenCV comes from the COOL image; Node 22.23.4 and Litestream 0.5.17 are checked by SHA 256 in
  `deploy/remote_setup.sh`.

## Documents

* [Technical report](docs/report.md)
* [Architecture](docs/architecture.svg) and [agent workflow](docs/agent-workflow.svg), with their Mermaid sources
  ([architecture.mmd](docs/architecture.mmd), [agent-workflow.mmd](docs/agent-workflow.mmd))
* [Video script](docs/video-script.md) and [Devpost text](docs/devpost.md)
* The bridge for recorders behind a router: [bridge/README.md](bridge/README.md)

## Licence

MIT, see [LICENSE](LICENSE). The models keep their own licences, listed above.
