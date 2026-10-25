# Watchful

Ask your cameras. Watchful is an AI CCTV operator: OpenCV 5 watches live streams, a browser webcam or uploaded
recordings; you say in plain words what matters ("tell me if grandma falls", "a car enters the parking lot after
10 pm", "someone trying to get in at night"); an agent checks every flagged moment with OpenCV tools before it
alerts; and you can ask about the footage ("when did the maid come into the kitchen and when did she leave?") or
find a person by an enrolled face, a photo given with consent, or a description.

Built for the OpenCV AI Competition 2026, powered by AWS. MIT licensed.

## What it does

* **Watches in plain words.** The agent compiles a request into a watch made of generic signals (entered, left,
  stayed, how many, posture, hands at a door, climbing, came back, looks like) combined as all, any or a weighted
  score. Nothing is hardcoded per watch type; presets such as fall, possible intruder, lookout and gate left open
  are just saved compositions in `backend/watchful/data/presets.json`.
* **Checked before it alerts.** Each candidate arrives with its evidence (best crop, frames around the moment, the
  track's path and poses). The agent looks, and can zoom and sharpen, brighten or denoise, draw the path, measure
  motion with optical flow, or read the pose history, all with OpenCV, before it raises the alert, dismisses it,
  or asks a person. If the AI is down, serious moments still alert as unverified.
* **Fast scans of recordings.** A video is split into time chunks that run on every core at once; tracks are
  stitched across chunk borders and reasoned in one ordered pass, so the result matches a single pass.
* **Find anything.** One semantic index covers whole scenes (a frame every few seconds while something moves) and
  every tracked object's best crop, embedded in the background with SigLIP 2 on the OpenCV DNN engine. Type
  "someone carrying a box in the kitchen this morning" or "people without helmets": the AI turns the words into a
  caption plus camera and time filters, the index ranks by image and text embeddings against a plain caption, and
  the AI confirms the best matches. A photo of a person searches by face, only with their consent.
* **Appearance watches.** "Tell me when someone is not wearing glasses", "a worker without a helmet": a looks_like
  signal compares each person with a caption and its contrast, and the agent checks the crop before alerting.
* **Questions.** Answers cite real recorded events (anything else is dropped) and say when two people look alike
  instead of guessing.
* **Smooth live view.** Video is sent at 10 frames per second with the boxes drawn in the browser; analysis runs
  underneath at 5. A browser webcam is shown straight from the camera with only the boxes coming back.
* **Camera health.** Covered, blurred or turned cameras are noticed (ORB matching against a reference view and a
  USAC homography).

## OpenCV 5 in Watchful

| Stage | OpenCV | Why |
| --- | --- | --- |
| Reading video | `VideoCapture` (FFmpeg), seeking by time, `grab` to skip frames, `imdecode` for webcam frames | Live streams, uploads and fast chunked scans |
| Resizing | `resize` (INTER_AREA) | Accelerated by KleidiCV on Graviton |
| Night mode | `createCLAHE` on the LAB lightness channel | Detection in the dark |
| Motion gate | `createBackgroundSubtractorMOG2`, `morphologyEx`, `findContours` | Idle frames skip detection |
| Detection | YOLOX on the new DNN engine (`readNetFromONNX`), `dnn.NMSBoxesBatched` | People, vehicles, bags, pets |
| Tracking | `KalmanFilter` per track, IoU association in two passes | Stable identities for zones and timers |
| Zones | `pointPolygonTest` | Enter, exit, dwell, counts |
| Pose and falls | MediaPipe pose on the DNN engine with `getRotationMatrix2D` and `warpAffine` crops | Falls, hands at a door, climbing |
| Faces | `FaceDetectorYN` (YuNet) and `FaceRecognizerSF` (SFace) | Only people enrolled with consent |
| Clothing | `calcHist` and `compareHist` on HSV | Linking sightings without a face |
| Embeddings | SigLIP 2 image and text towers on the DNN engine | Search by description, open vocabulary watches |
| Image quality | `Laplacian` variance | Sharpest crop for faces, embeddings and the AI |
| Tamper | `ORB_create`, `BFMatcher` ratio test, `findHomography` with `USAC_MAGSAC`, `meanStdDev` | Covered, blurred or moved cameras |
| Agent tools | `resize` (INTER_CUBIC) with unsharp masking, `fastNlMeansDenoisingColored`, CLAHE, `polylines`, `DISOpticalFlow` | Looking closer before deciding |

## AWS

| Service | Use |
| --- | --- |
| EC2 Graviton4 (COOL AMI when available) | API, camera workers and parallel scans |
| S3 | Clips, keyframes and enrolment photos; the retention job deletes clips and pictures after 7 days (enrolment photos stay until the person is removed) |
| CloudWatch | Frames per second, ms per frame, frames skipped, detector runs, AI calls, alerts, scan speed |
| SNS | Email to a caregiver when a person approves a serious alert, at most one per 10 minutes |
| Bedrock | Optional AI provider for the agent (`AI_PROVIDER=bedrock`) |
| CloudFormation and Budgets | One command deploy and a monthly budget alert |

## Swap anything

Every part is registered by name and picked by a setting (`backend/watchful/registry.py`,
`backend/watchful/components.py`):

| Setting | Choices |
| --- | --- |
| `DETECTOR` | `yolox_nano` (default), `yolox_tiny`, `yolox_s` |
| `POSE_MODEL`, `FACE_MODEL`, `EMBEDDER` | `mediapipe`, `yunet_sface`, `siglip2` |
| `AI_PROVIDER`, `OPENROUTER_MODEL`, `BEDROCK_MODEL` | `openrouter` or `bedrock`, any model id |
| `AI_MODEL_COMPILE`, `AI_MODEL_VERIFY`, `AI_MODEL_ASK` | a different model per task |
| `STORAGE`, `METRICS`, `NOTIFY` | `local`/`s3`, `log`/`cloudwatch`, `log`/`sns` |
| `DATABASE_URL` | SQLite (default `sqlite:///data/watchful.db`) or PostgreSQL; the schema comes from Alembic (`backend/migrations`) |
| `DNN_ENGINE` | `auto`, `new`, `classic` |

Thresholds live in one typed config, `backend/watchful/tuning.py`, overridable with a JSON file (`TUNING_FILE`)
and per camera. Prompts are files in `backend/watchful/agent/prompts/`.

## Run it

Requirements: Python 3.12, ffmpeg, Node 22.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock
python scripts/fetch_assets.py            # models and sample clips, checked by sha256
(cd web && npm ci && npm run build)
export SESSION_SECRET=... DATA_KEY=... OPENROUTER_API_KEY=... PUBLIC_BASE_URL=http://localhost:8000
PYTHONPATH=backend uvicorn watchful.api:create_app --factory --port 8000
PYTHONPATH=backend python -m watchful.auth signin-link you@example.com --workspace "Green Park"
```

Sign in with Google (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`) or with a single use link the operator prints on
the server, as above (`--workspace-id default` opens the demo society that `scripts/seed_demo.py` makes). Roles:
viewer, supervisor, admin, owner.

Tests: `pytest -q` (about 140 tests); the speed check is `pytest -q -m slow`.

Deploy to AWS: copy `deploy/params.env.example` to `deploy/params.env`, fill it in, then `deploy/deploy.sh`.

## Evaluation

`python eval/bench.py --label <machine> --instance <type>` measures live frames per second and the seconds to scan
a minute of 1080p video. `python eval/evaluate.py --ai eval/labels/*.json` measures precision, recall and F1 per
watch, enter and exit timing error, false alerts per hour against a plain motion alarm, and how few frames reach
the AI. Results and failure cases are in `docs/report.md`.

## Privacy and responsible use

* Faces are recognised only for people the owner enrols with a consent tick. Unknown faces are never stored;
  photo searches build a temporary index that is thrown away.
* Enrolments can be deleted at any time; clips, keyframes, uploads and events are deleted after 7 days.
* The possible intruder watch judges behaviour only (lingering, trying doors, climbing, coming back), never
  appearance, and says "possible"; a person decides what to do.
* Fall detection is an aid, not a medical or safety guarantee.

## Models and licenses

See `assets/README.md`. YOLOX (Apache 2.0), OpenCV Model Zoo models (Apache 2.0 and MIT), SigLIP 2 (Apache 2.0).
Ultralytics YOLO is not used.
