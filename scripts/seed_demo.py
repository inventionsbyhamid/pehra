"""Create the demo cameras described in a JSON file (default deploy/demo.json), replacing earlier demo cameras.

usage: PYTHONPATH=backend python scripts/seed_demo.py [deploy/demo.json]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from watchful.components import make_repo  # noqa: E402
from watchful.config import get_settings  # noqa: E402
from watchful.rules.presets import preset  # noqa: E402
from watchful.rules.watch import Watch  # noqa: E402
from watchful.store.db import Camera, WatchRow, new_id  # noqa: E402
from watchful.vision.zones import Zone  # noqa: E402


def main(spec_path: str = "deploy/demo.json") -> None:
    spec = json.loads(Path(spec_path).read_text())
    repo = make_repo(get_settings())
    for cam in repo.list_cameras():
        if cam.kind == "demo":
            cam.enabled = False
            repo.update_camera(cam)
    for c in spec["cameras"]:
        zones = [Zone(new_id("zone"), z["name"], [tuple(p) for p in z["polygon"]]) for z in c["zones"]]
        cam = repo.add_camera(Camera(None, c["name"], "demo", c["clip"], zones))
        for w in c.get("watches", []):
            watch = preset(w["preset"], **w.get("fill", {})) if "preset" in w else Watch.model_validate(w)
            repo.add_watch(WatchRow(None, cam.id, w.get("text", watch.name), watch.model_dump(), True))
        print(f"{cam.name}: {len(c.get('watches', []))} watches")


if __name__ == "__main__":
    main(*sys.argv[1:])
