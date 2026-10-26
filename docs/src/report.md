# Pehra technical report

Pehra is an AI control room for the CCTV cameras Indian housing societies already have, built on OpenCV 5 and AWS.
This short report covers how it is built, how OpenCV is used, how we evaluated it, and what it cannot do yet.

* Live demo: {{link.demo}} (press Try the demo)
* Video: {{link.video}}
* Code: {{link.repo}}

Every measured number here is filled in by `docs/fill.py` from a file in `eval/results/` and links to it. A number we
have not measured says "not measured yet". Settings and design figures are written plainly.

## 1. The problem

A housing society runs dozens of cameras and nobody watches them. At best a guard glances at one monitor, and footage
is looked at after something goes wrong
([Studio Matrx](https://www.studiomatrx.org/guides/cctv-control-room-monitoring-india)). Cameras die quietly: at one
Kharar society about 150 of 350 worked
([The Tribune](https://www.tribuneindia.com/news/chandigarh/residents-flag-rise-in-thefts-at-kharar-society/amp)).
Children have drowned in society pools left unattended
([The Tribune](https://www.tribuneindia.com/news/delhi/5-year-old-boy-drowns-in-gurugram-society-pool-643276),
[Deccan Herald](https://www.deccanherald.com/india/karnataka/bengaluru/seven-year-old-girl-drowns-in-swimming-pool-in-bengaluru-apartment-3062765)).
Gate apps log what guards type, not what cameras see
([MyGate help](https://help.mygate.in/articles/134062-why-are-the-visitors-still-coming-to-my-flat-without-my-approval)).

Pehra is for the committee or facility manager, who sets it up; the security supervisor, who reads the alerts and the
logs; and the guards in the society's Telegram group, who get alerts and can answer them without an account. No
society has used Pehra yet. The need comes from the reports above, not from our own users.

## 2. What it does

* A control room: every camera on one wall with a health dot, and one feed of incidents.
* Watches in plain words. "Someone in the water after pool hours" is compiled into general signals. Before saving,
  a card shows what will be watched and how reliably, what cannot be watched and the nearest alternative, the setup it
  needs, and how often it would have fired over the last 24 hours of that camera.
* An AI check of each flagged moment, by an agent that uses OpenCV tools. The AI never watches a stream.
* Graded alerts on Telegram, with a still picture, faces blurred, and Acknowledge and False alarm buttons.
* A gate log of vehicles by plate, a register of consenting adult staff (first seen and last seen each day; came in
  and went out only from crossing a line drawn with a direction), and alerts for cameras that go offline or are
  covered, turned or blurred.
* Cameras over RTSP, through a small bridge for recorders behind a router, from a phone or laptop browser (faces
  blurred on the device first), or as an uploaded video.

## 3. Architecture

![Pehra at a glance](overview.svg)

One Python service (FastAPI) holds the society API, sign in, live sockets and the AI check. Camera processes, one per
CPU by default, each run the OpenCV pipeline for several cameras. Scan workers take uploads in time chunks. A job
worker sends Telegram messages, makes blurred stills and the daily summary, and deletes old footage. Every row, file
and socket belongs to one society. Replaceable parts (detector, pose, faces, image model, AI, storage, database,
alerts, metrics) are registered by name and picked by a setting.

![Pehra architecture: the committee in a browser, a phone or laptop, the Pehra bridge and CCTV recorders over RTSP reach one EC2 Graviton4 instance with COOL in AWS Mumbai. On it the web app, the camera processes running OpenCV 5 (decode, motion gate, detect, track, zones and lines, pose, faces at entries, plates, camera health), the AI check with its OpenCV tools and the job worker. Flagged moments go to the AI check, incidents to the job worker, and alerts to the society's Telegram group. S3, Bedrock, Parameter Store, CloudWatch, SNS and CloudFormation complete the stack.](architecture.svg)

## 4. How OpenCV is used

Every pixel step goes through OpenCV 5: `opencv-python-headless` 5.0.0.93 in development and on the demo server,
COOL's build on Graviton on AWS, and OpenCV.js 5.0 in the browser. One exception is deliberate. SigLIP 2, the image
and text model for looks described in words, runs its forward pass on ONNX Runtime by default. On OpenCV DNN its image
tower added {{build.pool.siglip.image_opencv_mb}} to a process, on ONNX Runtime {{build.pool.siglip.image_ort_mb}},
with the same vectors (cosine {{build.pool.siglip.cosine}}). Its preprocessing stays in OpenCV, and
`EMBEDDER_RUNTIME=opencv` moves it back to `cv2.dnn`.

On each analysed frame ({{say:5 frames a second}} by default, at 960 px wide):

1. **Read.** `VideoCapture` with FFmpeg, or `imdecode` for pushed pictures after checking their size from the header;
   `resize` with `INTER_AREA`.
2. **Night.** A dark frame gets CLAHE on the lightness of Lab.
3. **Camera health.** Every few seconds a small grey copy is compared with a reference: `meanStdDev` for a covered
   lens, the variance of the `Laplacian` for blur, and ORB with a ratio test and a `USAC_MAGSAC` homography for a
   turned camera.
4. **Motion gate.** MOG2 on a blurred small copy, opening, dilation and contours. A still frame with nothing tracked
   skips the detector.
5. **Detection.** YOLOX nano through `cv2.dnn`, with `NMSBoxesBatched`.
6. **Tracking.** A `KalmanFilter` per track and three association passes: two by overlap, then one by distance over
   time, size and class, with an HSV histogram to break ties. The third pass keeps ids at the low frame rates a busy
   server delivers (section 6.3).
7. **Zones and lines.** `pointPolygonTest` on the foot point; directed lines with hysteresis, so a crossing counts
   once.
8. **Only when a watch needs it.** MediaPipe pose through `cv2.dnn` on an upright crop (`warpAffine`); YuNet and SFace
   on entry cameras; DIS optical flow inside a box against the flow around it (a still body in moving water); plates
   on vehicles near a gate line.

A watch is data: a subject, signals with conditions, hours, a severity and a question for the checker. Nothing about
pools or gates is in the code. The signals are general: presence, entering, crossing, dwell, count, still, vanished in
a zone, alone, a zone's state, looks described in words, plate attributes, speed, posture and camera health. A zone's
state (a gate open or closed) is matched against saved example pictures through a blurred CLAHE `Sobel` edge picture
and `matchTemplate`. The compiler checks every watch the AI writes against each signal's contract, so a watch that
saves can fire. With the real AI, {{build.compile.generic}} of {{build.compile.requests}} plain words requests
compiled into these signals with no new code.

**Plates.** `LPD-YuNet` through `cv2.dnn` finds the plate, `getPerspectiveTransform` and `warpPerspective` flatten it,
Otsu `threshold` splits two line plates, and `PP-OCRv5` reads each line through `cv2.dnn`, with the `PP-OCRv3` DB
detector (`TextDetectionModel_DB`) as a fallback. An Indian plate grammar fixes look alike characters, reads are voted
across frames, and a read under the confidence threshold stays unread. The colour comes from the HSV median of the
plate's background.

**Staff.** Three to five photos of a consenting adult become SFace templates; the photos are never stored. Matching
runs only on entry, gate and service door cameras.

**Privacy in the pixels.** Telegram stills pixelate every head that is not the enrolled person's, where pose or YuNet
finds it. On a phone used as a camera, OpenCV.js runs `FaceDetectorYN` on each frame and covers every face before the
picture is made; a frame is never sent unless detection ran on it.

## 5. The AI check

![Pehra agent workflow: a camera frame goes through OpenCV, the watch engine flags a moment or sends nothing, camera health and staff faces skip the AI, and the AI check calls OpenCV tools (more frames, zoom, brighten, denoise, flow, pose, path walked) in a loop until it alerts with a grade, asks a person, cannot judge, or dismisses. An incident goes to the control room and Telegram, and Acknowledge or False alarm comes back.](agent-workflow.svg)

Rules decide in code whether a moment is worth a look. Some moments never reach the AI: camera health and enrolled
faces are measured by OpenCV and filed at once. A critical moment goes to Telegram before the check, and the verdict
edits the message.

The checker (`agent/verifier.py`) sees the watch, what OpenCV saw and a few pictures, and answers with one tool call
a step. Each result goes back to it with any new picture, for at most {{say:6}} steps and {{say:45 s}}.

| Tool | What it runs |
| --- | --- |
| `get_frames(from_s, to_s, n)` | Frames around the moment with the zones and box drawn (`polylines`) |
| `crop_zoom(at_s)` | The object enlarged with `INTER_CUBIC` and an unsharp mask (`GaussianBlur`, `addWeighted`) |
| `enhance(image, kind)` | CLAHE, `fastNlMeansDenoisingColored` or sharpening |
| `motion_trail()` | The path walked, drawn on the latest frame |
| `flow(from_s, to_s)` | DIS optical flow in the box between frames at most {{say:0.6 s}} apart |
| `pose_history(seconds)` | The torso angle over time from MediaPipe pose |

It finishes by raising the alert with a grade and a reason, dismissing it, asking a person, or saying it cannot judge
because the question needs an age. If the AI is down, a reply is broken or the day's limit is used up, the moment is
still filed as an incident marked not checked by the AI. Each society has a daily limit, and so does the whole server.

We sent one flagged Plaza moment to the real model ({{agent.plaza.model}}) four times: twice as filmed, twice dimmed
to {{agent.plaza_dark.dark}} of its brightness. Every run raised the alert. On the dimmed pictures it looked closer
first ({{agent.plaza_dark.r0.tools}}); as filmed it did not ({{agent.plaza.r0.tools}}). That shows the loop on one
moment; it does not measure the agent's accuracy. Those runs also showed a bug: `flow` compared frames seconds apart
and read "{{agent.plaza.r0.s1.result}}" for a walking person. It now compares neighbouring frames and says when they
are too far apart. The traces were recorded before that fix.

## 6. Evaluation

### 6.1 Method

We have one labelled clip: OpenCV's sample `vtest.avi`, a busy plaza seen from above ({{eval.plaza.duration_s}}), the
same scene the demo camera plays. It is not a society camera. One of us labelled it by eye: every time someone's feet
entered the left walkway (`enter`, {{eval.plaza.truth.enter}} events), and every crossing of its edge going in
(`cross_in`, {{eval.plaza.truth.cross_in}}) or out (`cross_out`, {{eval.plaza.truth.cross_out}}).
`python eval/evaluate.py eval/labels/plaza.csv --fps 10,2,1` plays the clip through a camera process's own code and
every candidate through the app's own path, with a stand in for the AI that calls every moment real. A candidate
matches a label within {{eval.plaza.tolerance_s}}. Intervals are Wilson at {{say:95%}}.

### 6.2 Results

| Watch, {{say:2 fps}} | Labelled | Matched | False alarms | Missed | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- |
| Enters the left walkway | {{eval.plaza.fps_2.enter.truth}} | {{eval.plaza.fps_2.enter.tp}} | {{eval.plaza.fps_2.enter.fp}} | {{eval.plaza.fps_2.enter.fn}} | {{eval.plaza.fps_2.enter.precision}} | {{eval.plaza.fps_2.enter.recall}} |
| Crosses going in | {{eval.plaza.fps_2.cross_in.truth}} | {{eval.plaza.fps_2.cross_in.tp}} | {{eval.plaza.fps_2.cross_in.fp}} | {{eval.plaza.fps_2.cross_in.fn}} | {{eval.plaza.fps_2.cross_in.precision}} | {{eval.plaza.fps_2.cross_in.recall}} |
| Crosses going out | {{eval.plaza.fps_2.cross_out.truth}} | {{eval.plaza.fps_2.cross_out.tp}} | {{eval.plaza.fps_2.cross_out.fp}} | {{eval.plaza.fps_2.cross_out.fn}} | {{eval.plaza.fps_2.cross_out.precision}} | {{eval.plaza.fps_2.cross_out.recall}} |

| Every watch together | {{say:10 fps}} | {{say:2 fps}} | {{say:1 fps}} |
| --- | --- | --- | --- |
| Precision | {{eval.plaza.fps_10.all.precision}} ({{eval.plaza.fps_10.all.precision_ci}}) | {{eval.plaza.fps_2.all.precision}} ({{eval.plaza.fps_2.all.precision_ci}}) | {{eval.plaza.fps_1.all.precision}} ({{eval.plaza.fps_1.all.precision_ci}}) |
| Recall | {{eval.plaza.fps_10.all.recall}} ({{eval.plaza.fps_10.all.recall_ci}}) | {{eval.plaza.fps_2.all.recall}} ({{eval.plaza.fps_2.all.recall_ci}}) | {{eval.plaza.fps_1.all.recall}} ({{eval.plaza.fps_1.all.recall_ci}}) |
| Candidates, incidents, AI checks | {{eval.plaza.fps_10.ai.candidates}}, {{eval.plaza.fps_10.ai.incidents}}, {{eval.plaza.fps_10.ai.checks}} | {{eval.plaza.fps_2.ai.candidates}}, {{eval.plaza.fps_2.ai.incidents}}, {{eval.plaza.fps_2.ai.checks}} | {{eval.plaza.fps_1.ai.candidates}}, {{eval.plaza.fps_1.ai.incidents}}, {{eval.plaza.fps_1.ai.checks}} |

On a crowded scene the watches find about three of four labelled events at every rate, and precision drops only a
little as the rate drops. Grouping keeps the AI to one check per situation. A plain motion alarm cannot tell these
events apart: the plaza never stops moving, so on the whole clip it alerted this many times:
{{eval.plaza.fps_2.motion_alarm_alerts}}.

### 6.3 A failure we found and fixed

Our first shared camera pool gave about one analysed frame a second per camera on a loaded server. At that rate the
tracker lost people between frames: each walker became several ids, "enters" fired again and again, and line
crossings nearly vanished. The third association pass fixed it. For crossings going in at {{say:1 fps}}, recall went
from {{eval.plaza_before_fix.fps_1.cross_in.recall}} to {{eval.plaza.fps_1.cross_in.recall}}, and distinct person ids
from {{eval.plaza_before_fix.fps_1.person_ids}} to {{eval.plaza.fps_1.person_ids}}.
`backend/tests/test_track_plaza.py` keeps it fixed.

### 6.4 Failure cases

The evaluation saves the first false alarm and the first miss of each watch as pictures, with the tracker's boxes
drawn, in [eval/results/failures](../eval/results/failures/). What we know goes wrong:

* People walking close together, seen from above, merge into one box, so one of them is missed.
* At very low frame rates two people of the same size in similar clothes can swap ids.
* At the start of a clip, people already near a line are picked up as they first appear.
* A plate box that cuts characters can give a wrong but valid read.
* The age guard also refuses some harmless phrases, such as "a kids bicycle left on the ramp".

### 6.5 Plates, speed and memory

On {{plates.commons.photos}} Wikimedia Commons photos (handheld, daylight, not gate CCTV), the reader read
{{plates.commons.exact}} of {{plates.commons.plates}} plates exactly, {{plates.commons.wrong}} wrong, and left
{{plates.commons.unread}} unread. On a synthetic gate (a real car photo moved over a drawn gate),
{{build.plates.gate_runs_paired}} of {{build.plates.gate_runs}} runs logged one in and one out paired by plate.

`python eval/bench.py` runs the live camera pool on a busy Plaza clip. On our dev box (a 2013 Xeon without AVX2,
shared with other work), {{bench.devbox_6cpu_no_text.cameras}} cameras on {{bench.devbox_6cpu_no_text.vcpus}} CPUs
analysed {{bench.devbox_6cpu_no_text.fps_per_camera}} per camera in {{bench.devbox_6cpu_no_text.memory_total}} in all.
A watch described by looks on every camera is the expensive case, since the image model looks at crops. Embedding only
the tracks and zones a watch uses, once per look, raised that case from {{bench.devbox_6cpu_text_before.fps_per_camera}}
to {{bench.devbox_6cpu_text.fps_per_camera}} per camera on {{say:6 CPUs}}, and from
{{bench.devbox_2cpu_text_before.fps_per_camera}} to {{bench.devbox_2cpu_text.fps_per_camera}} on {{say:2 CPUs}}. On this
CPU it stays under our goal of one analysed frame a second per camera. None of these numbers is from Graviton.

## 7. AWS and COOL

Pehra runs on AWS at {{link.aws}}: one CloudFormation stack in Mumbai (`ap-south-1`), built by `deploy/deploy.sh`
and tested with `cfn-lint`, moto and a fake `aws` command. The checks below come from the real deploy.

| Service | Its job |
| --- | --- |
| EC2 {{aws.instance_type}} (Graviton4) | Runs everything; standard OpenCV 5 on our deploy, COOL's OpenCV 5 when the template is given the COOL image |
| S3 | Pictures, clips and a Litestream copy of the SQLite database. Object Lock is on for the bucket, but the app does not lock clips yet |
| Bedrock | One model through its global inference profile, the only one the role may call: Amazon Nova 2 Lite on our deploy, Claude Haiku 5.5 with Marketplace access |
| SSM Parameter Store | Settings and secrets, loaded when each service starts |
| CloudWatch | Logs, metrics per camera process, a dashboard, six alarms |
| SNS | Alarm emails |
| CloudFormation and Budgets | The stack, and a monthly budget alarm |

The deploy proves itself. With the COOL image it fails unless `cv2` is loaded from under `/opt/cool` with KleidiCV;
with a blank image it installs standard OpenCV 5 on Amazon Linux 2023 and fails unless that is OpenCV 5 on arm64. Both
fail unless H.264 and H.265 decode and the app stays inside its memory budget. Our deploy wrote `eval/results/aws.json`:
OpenCV {{aws.opencv}}, loaded from COOL {{aws.cool}}, KleidiCV {{aws.kleidicv}}, memory {{aws.memory_mb}} of
{{aws.memory_budget_mb}}. It runs standard OpenCV because our AWS India account can't subscribe to Marketplace
products yet (AWS Marketplace doesn't take saved cards or UPI from AWS India accounts), which also rules out COOL and
Claude there for now. Bedrock on our new account was still in AWS's verification at submission; until it clears,
moments on the AWS copy are filed as not checked by the AI. Running the instance is estimated at about {{build.estimates.aws_month_usd}} a month at list
price, before Bedrock, S3 and transfer. That is an estimate, not a bill.

**COOL.** `eval/bench_aws.sh --yes-spend` starts temporary instances and runs the same camera pool benchmark on a
c8g.large with COOL, on the same instance with stock OpenCV 5.0.0.93, and on a c7i.large with stock OpenCV, then
terminates them. Every frame is resized, converted, blurred, run through MOG2 and morphology and often CLAHE, which is
the imgproc work COOL targets; the DNN forwards are most of a busy frame's time. COOL over stock OpenCV on the same
Graviton instance: {{bench.speedup.cool_over_stock_no_text}}. Per camera on a c8g.large with COOL:
{{bench.c8g_cool_no_text.fps_per_camera}}. Both fill in when the benchmark runs on AWS.

The same app also runs anywhere with Docker. The root `Dockerfile` and `compose.yaml` build an image of
{{build.selfhost.image_mb}} with every model, which we built and started on our own server.

## 8. Privacy and responsible use

* Faces only for consenting adult staff, matched only on entry, gate and service door cameras. Photos are never
  stored, and deleting a person reaches every camera process at once. Residents and visitors are never identified,
  and there is no search by photo.
* Never children. No child is enrolled, identified or aged. A watch about age is refused in layers: words in English,
  Hindi and other Indian languages and scripts, age and height patterns, a SigLIP 2 meaning check, and a flag the
  compiler model must set. The checker never states an age, and its text is checked again.
* Telegram gets stills, not video, with faces blurred. Clips stay behind sign in, by role, and every view is recorded.
* Footage, incidents and logs are deleted on each plan's schedule. A printable notice is made for each camera zone.
  Under India's DPDP Act the society is the data fiduciary and Pehra its processor.
* The AI sees only flagged moments, with daily caps per society and for the whole server. Alerts the AI checked say
  "Possible"; a person decides what to do. The site calls Pehra an aid, not a guarantee.

## 9. Limits and what is not done

* One labelled clip, labelled by one of us. No society, gate, pool or staff footage is labelled yet
  (`eval/record_checklist.md` is the plan).
* Every speed and memory number is from our x86 dev box. AWS, COOL and Graviton are not measured yet.
* The agent's accuracy is not measured: the evaluation used a stand in, and the real model ran on one moment.
* Thresholds for still in water, zone states and plates were set on rendered or sample pictures.
* `LPD-YuNet` was trained on Chinese plates. Older, army, diplomatic and temporary plates are left unread on purpose.
* Small, profile or infrared faces are often missed, so a still may show a pixelated person rather than a sharp scene.
* One server and one app worker; Postgres is planned and untested.
* Billing through Polar is tested against its SDK's types and has never been called. Telegram is tested against a
  mocked Bot API, and the demo server now has a real bot connected.

## 10. Reproduce

```
python scripts/fetch_assets.py                                # every model, checked by SHA 256
PYTHONPATH=backend pytest -q                                  # the backend tests
python eval/evaluate.py eval/labels/plaza.csv --fps 10,2,1    # section 6
python eval/bench.py --setup devbox --cameras 8 --cpus 2      # section 6.5
eval/bench_aws.sh                                             # the COOL plan and its cost; starts nothing
python3 docs/fill.py                                          # every number into the documents
```

The README has the self hosting and AWS steps.
