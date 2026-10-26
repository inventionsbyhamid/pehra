# Pehra video script

Five minutes at most (this plan runs 4:50). Recorded by Hamid with the real product: screen recordings of the site
and the app, a phone filmed or screen recorded for the QR scene, and Hamid on camera at the start. Captions carry
the words; there is no narration unless Hamid wants it, and then a voice reads only the captions, honestly. Nothing
is said that is not built, measured or sourced. Every number below is filled from `eval/results/` by `docs/fill.py`; a
line that reads "not measured yet" is cut, never guessed.

## What to have ready

* **The live demo** at {{link.demo}}, freshly deployed, in a browser at desktop width with sound off. Press Try the demo
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
| 4:50 to 4:55 | End | The demo address and the repository | "Try it: {{link.demo}}" | |

## Numbers for the results card

Use these exactly as filled. Cut any line that reads "not measured yet".

* Plaza clip at {{say:2 fps}}, every watch together: precision {{eval.plaza.fps_2.all.precision}}, recall
  {{eval.plaza.fps_2.all.recall}}
* The same clip, line crossings going in at {{say:1 fps}}: recall {{eval.plaza_before_fix.fps_1.cross_in.recall}}
  before the tracker fix, {{eval.plaza.fps_1.cross_in.recall}} after
* Candidates the rules raised, and the AI checks they became: {{eval.plaza.fps_2.ai.candidates}} and
  {{eval.plaza.fps_2.ai.checks}}
* Plates read exactly on real photos (not gate CCTV): {{plates.commons.exact}} of {{plates.commons.plates}}
* COOL over stock OpenCV on the same Graviton instance: {{bench.speedup.cool_over_stock_no_text}}
* Cameras a c8g.large with COOL gives {{say:2 frames a second}}: {{bench.c8g_cool_no_text.cameras_at_2fps}}

## Notes for the edit

* Keep it calm: no music over the captions, no zooms that hide the screen.
* Only Hamid and the people in OpenCV's sample clip appear; no resident, no child, no real gate CCTV of others
  unless they consented.
* If AWS is not deployed in time, cut the COOL numbers and say nothing about Graviton speed.
* Upload as public or unlisted, at most 5:00, and put the link in `docs/links.json` as `video`.
