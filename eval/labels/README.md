# Labels

One JSON file per clip. Times are seconds from the start of the clip.

```json
{
  "clip": "data/eval/kitchen_1.mp4",
  "zones": [{"id": "z1", "name": "kitchen", "polygon": [[0.1, 0.2], [0.6, 0.2], [0.6, 0.9], [0.1, 0.9]]}],
  "watches": [{"preset": "enters", "fill": {"zone": "kitchen"}}, {"preset": "fall"}],
  "truth": {"Enters kitchen": [[12.0, 14.0], [80.0, 82.0]], "Fall": []},
  "enter_exit": [["enter", "kitchen", 12.6], ["exit", "kitchen", 61.0]],
  "notes": "daylight, one person, consented"
}
```

`truth` maps a watch name to the intervals when it should alert. Clips we record ourselves, with consent,
live outside git under `data/eval/`.
