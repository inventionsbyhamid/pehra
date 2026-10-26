# Labels

Each clip has two files here, named after it.

`<clip>.csv` holds what happened, one moment per row, in seconds from the start of the clip. Lines starting with `#`
are notes.

```
t_s,event
42,enter
42,cross_in
71.5,cross_out
```

`<clip>.json` says how to score it: the clip, its zones and lines (normalized 0..1, as in the app), which watch answers
which event, and who labelled it.

```json
{
 "clip": "eval/clips/entry_line.mp4",
 "labelled_by": "the owner, from notes taken while filming, checked against the clip",
 "zones": [
  {"name": "doorway", "polygon": [[0.3, 0.4], [0.7, 0.4], [0.7, 1], [0.3, 1]]},
  {"name": "door line", "kind": "line", "polygon": [[0.3, 0.6], [0.7, 0.6]]}
 ],
 "watches": [
  {"event": "enter", "preset": "enters", "fill": {"zone": "doorway"}},
  {"event": "cross_in", "watch": {"name": "Crosses the door line going in", "subject": {"classes": ["person"]},
   "conditions": [{"signal": "crossed", "zones": ["door line"], "params": {"direction": "in"}}]}}
 ],
 "skip_start_s": 3,
 "skip_end_s": 1,
 "tolerance_s": 2
}
```

A candidate matches a label of its watch's event within `tolerance_s`, one to one. The first `skip_start_s` and last
`skip_end_s` seconds are not scored (tracks need a few frames to start, and a moment at the very end has no frames
after it); matching still looks past both ends, so a moment just outside is neither a false alarm nor a miss. A line's
inside is on the right of its first point to its second, as seen on screen.

`plaza.csv` and `plaza.json` label OpenCV's sample clip vtest.avi, the Plaza demo camera. We labelled it ourselves, by
eye from frames, so it checks the engine on a busy public scene; it is not an independent benchmark.

`plates-commons.csv` holds the plate reader's reads of 8 Wikimedia Commons photos from Task 6a's run; `eval/plates.py`
scores it, or reads a folder of new photos itself (`--photos DIR` with `DIR/truth.csv`:
`photo,truth,colour_truth,light,in_grammar`).

Owner recorded clips go in `eval/clips/` (not in git); `eval/record_checklist.md` says what to film.
