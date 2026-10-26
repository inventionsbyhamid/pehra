# Recording kit

What to film for the demo cameras and for the evaluation, with a laptop and a phone. Every clip feeds two things: the judges' demo (it loops as a live camera) and the evaluation (counts with Wilson intervals, failure frames shown).

## Rules for every clip

* Adults only, each one has agreed in writing to be filmed (one line each: name, date, "I agree to be filmed for the Pehra demo and evaluation"). Never film a child, and never stage anything near water with a child.
* Mount the phone high and still, the way a CCTV camera looks down: a shelf, a grille, a tripod on a table. Landscape. 720p or 1080p. Do not hold it.
* Keep each clip 1 to 3 minutes. Note the time of each event as you film it (a notes app is fine): "0:42 walks in", "1:10 lies down".
* Plates and faces of people who did not agree are blurred before anything is published. Raw clips stay private.
* Put files in `data/samples/demo/` with the names below. A camera appears in the demo as soon as its file is there.

## Demo cameras

| File | What to film | Events to note |
|---|---|---|
| `demo/main_gate.mp4` | The society gate or the building entrance from above, cars and people passing | Each car or two wheeler in and out; each person in and out |
| `demo/service_gate.mp4` | A side entrance or a door where staff come in; you walking in and out as the "staff" (enrol yourself in People first) | Each arrival and leaving |
| `demo/pool.mp4` | Only if you have a pool with adult volunteers and a spotter: a swimmer, then someone floating still for 20 s, then the pool empty after hours | Swim start, still float start and end, last person out |

If there is no pool, skip it; the demo leaves the Pool camera out rather than fake it.

## Evaluation clips (same rules)

| Scenario | Positives | Negatives |
|---|---|---|
| Entry line | 20 walks in and out across a doorway | 10 walks along the doorway without crossing |
| Staff register | You (enrolled) arrive and leave 10 times | 10 passes by another consenting adult who is not enrolled |
| Fall or still | Lie down on a mat and stay still 20 s, 5 times | Sit down, bend to tie a shoe, kneel, 5 times each |
| Camera health | Cover the lens with a hand 5 s, 5 times; turn the phone away 3 times | Lights off and on once (should not count as covered) |
| Gate vehicles | 10 minutes of traffic at a gate by day, 10 by night, from a window or balcony | |
| After hours | Someone walks through a corridor after 10 PM, 5 times | |
| Delivery look | Red t shirt with a bag, 10 passes | You in red without a bag, 10 passes |

## After filming

1. Copy the files to `data/samples/demo/` (demo) or `eval/clips/` (evaluation; gitignored).
2. Write the notes as `eval/labels/<clip>.csv` with columns `t_s,event` (for example `42,enter`). Rough times are fine; matching allows 2 seconds.
3. Run the evaluation (`python eval/evaluate.py`); results land in `eval/results/`, and the docs quote only those files.
