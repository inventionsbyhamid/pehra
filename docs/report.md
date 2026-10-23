# Watchful: an AI CCTV operator built on OpenCV 5 and AWS

Technical report, OpenCV AI Competition 2026. Numbers marked FILL come from `eval/results/` and are filled in
only after they are measured.

## 1. Problem and users

Most cameras record but nobody watches them, and motion alerts are mostly noise. Families caring for someone
older, small shops and buildings want to know a few specific things: someone fell and did not get up, a car came
into the parking area, someone is trying the back door at night, when the maid arrived and left. Watchful lets
them say what matters in plain words, watches live cameras and recordings with OpenCV 5, has an agent check the
moments that might matter, and answers questions about the footage with times and pictures.

## 2. What it does

* Watches in plain words, compiled into compositions of generic signals.
* An agent that verifies every candidate with OpenCV tools before alerting, and asks a person before anything
  is sent outside.
* Fast scans of recordings: time chunks on every core, stitched tracks, one ordered reasoning pass.
* Questions about the footage, grounded in recorded events; find a person by face (consented), photo (consented)
  or description.
* Camera tamper alerts, night mode, a privacy model built on consent.

## 3. Architecture

![Architecture](architecture.png)

One Python service (FastAPI) runs a worker process per live camera and a process pool for scans. Every part is
registered by name and chosen by settings (detector, pose, faces, embedder, AI provider and model per task,
storage, database, metrics, notifications), so the same code runs locally (SQLite, local files) and on AWS
(DynamoDB, S3, CloudWatch, SNS, Bedrock).

## 4. OpenCV 5 implementation

### 4.1 Perception per frame
Motion gate (MOG2 at 320 px, morphology, contours); YOLOX on the new DNN engine with batched NMS; detection on
every second analysed frame while only tracked objects move, with new motion forcing a detection; Kalman and IoU
tracking in two passes (ByteTrack style); zones with `pointPolygonTest`; MediaPipe pose on rotated crops built with
`getRotationMatrix2D` and `warpAffine` (a wide box is tried turned both ways), run only for boxes that look like a
fall unless a watch needs hands or climbing; YuNet and SFace for enrolled people only; HSV clothing signatures;
the sharpest crop chosen by Laplacian variance; CLAHE night mode; tamper detection with ORB, a ratio test and a
USAC MAGSAC homography against a reference view.

### 4.2 Falls
A per track state machine: falling when the hip drops faster than 0.6 body heights per second while the torso
turns more than 30 degrees within a second (or, without pose, when the box centre drops fast and the box turns
wide), fallen when the body stays near horizontal for 2 seconds, recovered when upright again for 2 seconds.

### 4.3 Generic watches
Signals are registered functions over a track or the whole view (present, entered, exited, in_zone, dwell_s,
count, speed, posture, zones_visited, hands_in_zone_s, climbing, returns, zone_looks_like). A watch combines
conditions as all, any, or a weighted score with a threshold; subjects filter by class, by who (enrolled,
unknown) and by appearance (looks_like text or same_as a person seen earlier, both matched with SigLIP 2
embeddings on the OpenCV DNN engine). Presets are data. The compiler prompt is filled from the catalog, so new
signals reach it without code.

### 4.4 Fast scans
The video is cut into chunks (one or more per core, 2 s overlap). Each worker decodes only its range with
`VideoCapture`, samples the analysis rate on a shared time grid, and perceives. Tracks are joined across borders
by mean box IoU in the shared seconds, with a position fallback; the merged observations replay through the same
reasoner and rules as live cameras. On the CPU used for development, a crowded minute of 1080p went from 93 s to
16 s through these steps: YOLOX Nano instead of Tiny, pose only when needed, clips cut only when an alert is
opened, one worker per available core, the detector stride. Graviton results: FILL.

### 4.5 Find anything
A frame is stored every 3 s while something moves (once a minute otherwise) and every finished track keeps its best
crop; a background indexer embeds both with SigLIP 2 on the OpenCV DNN engine, so cameras and scans never wait.
A search is turned by the AI into a caption plus camera, time and class filters, ranked by SigLIP's own sigmoid
calibration p = sigmoid(exp(4.72) cos - 16.77) (read from the released checkpoint), and the top matches are
confirmed by the AI in one call. Searches about something missing ("without a helmet") compare the caption with
its contrast. A photo of a person searches by face, only with consent.

### 4.6 Live view
Video goes to the browser at 10 frames per second as plain JPEG with the latest boxes as data, drawn on a canvas;
analysis runs at 5. A webcam is shown straight from the browser's camera and only boxes come back.

### 4.7 Embeddings in OpenCV
SigLIP 2 base image and text towers run in OpenCV 5's new DNN engine. We also tried Google's EmbeddingGemma 2:
it runs in OpenCV DNN after clipping Tanh inputs (OpenCV 5.0 `Tanh` returns NaN for large inputs), but its image
path was five times slower and did not rank our CCTV crops better.

## 5. The agent (Agentic Vision)

![Agent workflow](agent-workflow.png)

Each step the model returns one JSON tool call. OpenCV tool results change the next step: a dark frame leads to
`enhance`, a small person to `crop_zoom`, a possible fall to `pose_history` or `flow` (DIS optical flow inside the
box). Terminal tools: raise_alert, dismiss, ask_human. Capped at 6 steps and 45 seconds; on failure a critical
candidate becomes an unverified alert with the OpenCV evidence. Every step is stored and shown with the alert.

## 6. AWS deployment

EC2 Graviton4 (FILL: COOL AMI or stock OpenCV 5), S3 with a 7 day lifecycle, DynamoDB, CloudWatch metrics, SNS
email on approved alerts, optional Bedrock, CloudFormation with a budget alarm, one command deploy.

COOL evidence: FILL (frames per second and seconds per minute of 1080p on Graviton with COOL, Graviton with stock
OpenCV 5, and x86 of the same size; cost per camera hour).

## 7. Evaluation

Clips recorded with consent: FILL (list, durations, conditions).

| Watch | Precision | Recall | F1 |
| --- | --- | --- | --- |
| FILL | | | |

Enter and exit timing error: FILL. False alerts per hour against a plain motion alarm: FILL. Share of frames that
reached the AI: FILL.

### Failure cases
FILL from `eval/results/eval.json` (false alerts and misses with times and reasons).

## 8. Responsible use

Faces only for people enrolled with consent; unknown faces never stored; photo search builds a temporary index;
deletion at any time; 7 day retention; intruder scoring by behaviour only; human approval before any outward
message; fall detection is an aid, not a guarantee.

## 9. Limitations

FILL after evaluation (expected: very low light, heavy occlusion, people who look alike, partial falls behind
furniture, wide angle lenses near the frame edge, crowded scenes).
