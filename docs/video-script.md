<!-- Made by docs/fill.py from docs/src/video-script.md. Edit that file, then run python3 docs/fill.py. -->

# Pehra video script

Five minutes at most (this plan runs 4:50). Recorded by Hamid with the real product: screen recordings of the site
and the app, a phone filmed or screen recorded for the QR scene, and Hamid on camera at the start. Captions carry
the words; there is no narration unless Hamid wants it, and then a voice reads only the captions, honestly. Nothing
is said that is not built, measured or sourced. Every number below is filled from `eval/results/` by `docs/fill.py`; a
line that reads "not measured yet" is cut, never guessed.

## What to have ready

* **The live demo** at https://pehra.dijmo.com, freshly deployed, in a browser at desktop width with sound off. Press Try the demo
  once beforehand to check the Plaza camera loads.
* **A phone** with its camera free, to scan the QR code. Hamid's face is the only face on it, and it is blurred on
  the phone before sending.
* **Optional, if recorded and consented by then:** Hamid's clips `pool.mp4`, `main_gate.mp4` and `service_gate.mp4`
  in `data/samples/demo/` (see `eval/record_checklist.md`); with them the Pool and gate cameras and real gate log rows
  appear in every new sandbox. Without them, film the gate log as it is (empty in a sandbox until a gate clip exists)
  and say so in the caption, and film the staff register's made up staff.
* **A Telegram group** with the Pehra bot, only if a bot token is set; otherwise film the incident page's
  "On Telegram" preview, which shows exactly what the group would get.
* **For the AWS scene:** if deployed, the deploy's last lines with "COOL yes" and the CloudWatch dashboard; if not,
  `deploy/deploy.sh --dry-run` and the passing deploy tests.
* **The documents:** `docs/architecture.svg`, `docs/agent-workflow.svg` and the filled `docs/report.md` at sections 6
  and 7.

## Scenes

| Time | Scene | On screen | Caption | Needs |
| --- | --- | --- | --- | --- |
| 0:00 to 0:12 | Title and team | Hamid on camera, then the Pehra mark | "Pehra: an AI control room for the cameras a housing society already has. Built by Hamid Mahmood Siddiqui, India, for the OpenCV AI Competition 2026." | Hamid on camera |
| 0:12 to 0:32 | The problem | The landing page: the four pains with their sources (nobody watching, dead cameras, the gate, pools) | "Societies have dozens of cameras and nobody watches them. Dead cameras go unnoticed. Gate apps log what guards type, not what cameras see." | Live site |
| 0:32 to 0:50 | Try the demo | Press Try the demo; the sandbox opens on the control room: the camera wall, the Plaza tile, the alert feed with an open critical incident marked Example | "Try the demo gives every visitor a private society for a day." | Live demo |
| 0:50 to 1:15 | The control room | Open the Plaza camera: live frames with boxes, its health, its watches as plain sentences; back to the wall | "Every camera live, with its health. OpenCV 5 watches every frame; the AI never sees the stream." | Live demo |
| 1:15 to 1:55 | Typing a watch | Watch editor: type "tell me when a child is alone at the walkway" and show the card's Can't watch with the nearest instead; then type "someone walks into the left walkway"; the card shows Will watch with its tier, setup, the level and the last 24 hours | "Any watch in plain words. Pehra says what it will watch, what it cannot, and how often it would have fired yesterday. It never judges anyone's age." | The real AI on the demo server |
| 1:55 to 2:15 | The coverage card and saving | Pick Critical, Save; the watch appears on the camera page | "Saved as a composition of general OpenCV signals; no new code for a new event." | Live demo |
| 2:15 to 2:50 | An incident | The watch fires on the Plaza clip; the control room feed shows it; open the incident: the picture, the level, what happened in order, the AI check's steps | "OpenCV flags the moment; the agent looks closer with OpenCV tools, then grades it." | Live demo |
| 2:50 to 3:10 | The Telegram message | The incident page's "On Telegram" preview (or the real group): the still with faces blurred, the reason, Acknowledge and False alarm; press Acknowledge, the message changes | "One message per incident, edited as it changes. Faces of anyone not enrolled are blurred." | Live demo, or a bot token |
| 3:10 to 3:30 | Gate log and staff register | Logs: the gate log (rows with plates if a gate clip exists, else empty, captioned so), then the staff register | With a gate clip: "Every vehicle in and out, with its plate and colour." Without: "The gate log fills from a gate camera; this demo has none yet." Then: "Staff arrivals from consenting adult staff only." | A gate clip, if recorded |
| 3:30 to 3:55 | Add your phone by QR | Demo bar, Add your camera, A phone: the QR code; scan it; the phone page shows its notice and Start; Hamid's face is blurred on the phone; the new tile goes live; a typed watch fires | "Any phone becomes a camera. Faces are blurred on the phone before anything is sent." | A phone |
| 3:55 to 4:15 | Architecture and the agent | `architecture.svg`, then `agent-workflow.svg` and its loop between the AI check and the OpenCV tools | "Every pixel step runs on OpenCV 5. The agent calls OpenCV tools and reads each result before its next step." | The diagrams |
| 4:15 to 4:35 | Evaluation and failure cases | The report's section 6 tables, then one failure picture from `eval/results/failures/` | Only the numbers below, each as filled; then "Our labels, made by eye, on OpenCV's sample clip: not yet a society camera." | The filled report |
| 4:35 to 4:50 | AWS | If deployed: the deploy output with "COOL yes" and the CloudWatch dashboard. If not: the dry run and the stack's tests passing | Deployed: "On AWS Graviton with COOL, in Mumbai." Not deployed: "The AWS deployment is built and tested; it has not run yet." | AWS, if deployed |
| 4:50 to 4:55 | End | The demo address and the repository | "Try it: https://pehra.dijmo.com" | |

## Numbers for the results card

Use these exactly as filled. Cut any line that reads "not measured yet".

* Plaza clip at 2 fps, every watch together: precision [86%](../eval/results/eval-plaza.json "runs.fps_2.all.precision.value"), recall
  [73%](../eval/results/eval-plaza.json "runs.fps_2.all.recall.value")
* The same clip, line crossings going in at 1 fps: recall [0%](../eval/results/eval-plaza_before_fix.json "runs.fps_1.watches.cross_in.recall.value")
  before the tracker fix, [83%](../eval/results/eval-plaza.json "runs.fps_1.watches.cross_in.recall.value") after
* Candidates the rules raised, and the AI checks they became: [43](../eval/results/eval-plaza.json "runs.fps_2.ai.candidates") and
  [3](../eval/results/eval-plaza.json "runs.fps_2.ai.checks")
* Plates read exactly on real photos (not gate CCTV): [5](../eval/results/plates-commons.json "all.exact") of [7](../eval/results/plates-commons.json "all.plates")
* COOL over stock OpenCV on the same Graviton instance: not measured yet
* Cameras a c8g.large with COOL gives 2 frames a second: not measured yet

## Notes for the edit

* Keep it calm: no music over the captions, no zooms that hide the screen.
* Only Hamid and the people in OpenCV's sample clip appear; no resident, no child, no real gate CCTV of others
  unless they consented.
* If AWS is not deployed in time, cut the COOL numbers and say nothing about Graviton speed.
* Upload as public or unlisted, at most 5:00, and put the link in `docs/links.json` as `video`.
