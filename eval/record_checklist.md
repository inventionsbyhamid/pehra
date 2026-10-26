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

If there is no pool, skip it: `python scripts/fetch_assets.py --demo` puts a Pexels stock video of adults in a pool
there instead (credited in the app), and a recording of your own replaces it.

## Evaluation clips (same rules)

Name each file after its scenario (`eval/clips/entry_line.mp4`, `eval/clips/gate_night.mp4`). Note each event with the
event word in the last column; those are the words the labels use.

| Scenario | Positives | Negatives | Event words |
|---|---|---|---|
| Entry line | 20 walks in and out across a doorway | 10 walks along the doorway without crossing | `cross_in`, `cross_out`, `enter` |
| Staff register | You (enrolled) arrive and leave 10 times | 10 passes by another consenting adult who is not enrolled | `staff_in`, `staff_out` |
| Fall or still | Lie down on a mat and stay still 20 s, 5 times | Sit down, bend to tie a shoe, kneel, 5 times each | `fall` (when you are down) |
| Camera health | Cover the lens with a hand 5 s, 5 times; turn the phone away 3 times | Lights off and on once (should not count as covered) | `covered`, `moved` |
| Gate vehicles | 10 minutes of traffic at a gate by day, 10 by night, from a window or balcony | | `vehicle_in`, `vehicle_out` |
| After hours | Someone walks through a corridor after 10 PM, 5 times | | `enter` |
| Delivery look | Red t shirt with a bag, 10 passes | You in red without a bag, 10 passes | `look` (with the bag only) |

For plates, also take still photos of parked cars' plates (yours, or with the owner's agreement): 10 by day and 10 at
night, from where the gate camera would be. Put them in a folder with `truth.csv` (`photo,truth,colour_truth,light,in_grammar`,
for example `car1.jpg,MH12AB1234,white,night,yes`).

Film two takes of each scenario. Tune nothing on the second take: it is scored once, as it comes.

## After filming

1. Copy the files to `data/samples/demo/` (demo) or `eval/clips/` (evaluation; not in git).
2. Write the notes as `eval/labels/<clip>.csv` with columns `t_s,event` (for example `42,enter`). Rough times are fine;
   matching allows 2 seconds.
3. Copy `eval/labels/plaza.json` to `eval/labels/<clip>.json` and set the clip, its zones and lines, and which watch
   answers which event word (`eval/labels/README.md`).
4. Run `python eval/evaluate.py eval/labels/<clip>.csv --fps 5,2` and, for plates,
   `python eval/plates.py --photos <folder> --name gate`. Results land in `eval/results/` with pictures of the first
   false alarm and the first miss of each watch, and the docs quote only those files (`python3 docs/fill.py`).
