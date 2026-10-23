# Watchful (Devpost draft)

## Inspiration

Most cameras record but nobody watches them, and "motion detected" alerts are mostly noise. When someone you care
for lives alone, or a shop shuts for the night, you want to know a few specific things: did someone fall and not
get up, is someone trying the back door, when did the maid arrive and leave. We wanted a camera you can simply tell
what matters.

## What it does

* **Watches in plain words.** "Tell me if grandma falls", "a car enters the parking lot after 10 pm", "someone not
  wearing a helmet in the workshop". The agent builds each watch from generic signals, with no code per watch type.
* **Checked before it alerts.** Every flagged moment goes to an agent that looks at the evidence and can zoom,
  brighten, draw the path, measure motion with optical flow or read the pose history, all with OpenCV, before it
  alerts, dismisses, or asks a person. Serious alerts need a person's approval before an email goes out.
* **Find anything.** "Someone carrying a box in the kitchen this morning", "a white van". One semantic index of
  scenes and objects, searched by words or a photo.
* **Ask about the footage.** "When did the maid come into the kitchen and when did she leave?" Answers cite
  recorded moments and say when two people look alike.
* **Fast.** A minute of recorded video is scanned in FILL seconds on AWS Graviton.
* **Private by design.** Faces only for people enrolled with consent, 7 day retention, intruder alerts that judge
  behaviour and never appearance.

## How we built it

* OpenCV 5 for every pixel step: MOG2 motion gating, YOLOX on the new DNN engine, Kalman tracking, zones, MediaPipe
  pose, YuNet and SFace, SigLIP 2 embeddings on the DNN engine, CLAHE night mode, ORB and USAC homography tamper
  checks, DIS optical flow in the agent's tools.
* Fast scans split a video into time chunks across every core, stitch tracks across chunk borders and reason once,
  so the result matches a single pass.
* A provider neutral agent (DeepSeek on OpenRouter during development, Amazon Bedrock switchable) with a JSON step
  protocol, grounded answers and fallbacks when the AI is unavailable.
* FastAPI, React and TypeScript; AWS EC2 Graviton4 with COOL, S3, DynamoDB, CloudWatch, SNS, CloudFormation.
* Everything swappable by a setting: detector, pose, faces, embedder, AI provider and model per task, storage,
  database.

## Challenges we ran into

* Speed on CPU: from 93 s to 16 s for a crowded minute of 1080p on our old development server, by profiling every
  stage (YOLOX Nano at 320 px, pose only when a box looks like a fall, clips cut on demand, one decode thread per
  worker, detector stride).
* The agent first dismissed real events because it could not see the zones: we now draw zones and the object's
  box on every frame it looks at.
* EmbeddingGemma 2 in OpenCV: it runs once Tanh inputs are clipped (OpenCV 5.0 returns NaN for large inputs), but
  was five times slower than SigLIP 2 on CPU.
* WebSockets worked in tests but not in production until we tested against a real server.

## Accomplishments that we're proud of

FILL with measured accuracy and speed.

## What we learned

* Let OpenCV do the always on work and the AI only the judgement: FILL share of frames ever reach the AI.
* Calibrated similarity (SigLIP's own sigmoid) beats hand picked thresholds for open vocabulary search.

## What's next

Phone app, multi camera timelines, on device edge mode, more languages.

## Built with

opencv, python, fastapi, react, typescript, aws-ec2, graviton, aws-s3, dynamodb, cloudwatch, sns, cloudformation,
bedrock, openrouter, deepseek, yolox, mediapipe, siglip
