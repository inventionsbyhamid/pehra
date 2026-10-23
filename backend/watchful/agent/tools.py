"""Tools the verifier agent can call. Each one runs OpenCV on the evidence and returns text and images."""
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from watchful.store.blobs import BlobStore


@dataclass
class ToolResult:
    text: str
    images: list[str] = field(default_factory=list)


@dataclass
class ToolContext:
    repo: object
    blobs: BlobStore
    camera_id: str
    frames: Callable[[float, float, int], list[bytes]]
    crops: Callable[[int, float], bytes | None]
    poses: Callable[[int, float], list[dict]]
    boxes: Callable[[int], list[tuple[float, tuple]]] = lambda tid: []
    images: dict[str, bytes] = field(default_factory=dict)

    def add_image(self, data: bytes) -> str:
        ref = f"img{len(self.images) + 1}"
        self.images[ref] = data
        return ref


def _decode(data: bytes) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def _encode(img: np.ndarray) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


def _sharpen(img: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(img, (0, 0), 2.0)
    return cv2.addWeighted(img, 1.6, blur, -0.6, 0)


def get_frames(ctx: ToolContext, t0: float, t1: float, n: int = 3) -> ToolResult:
    refs = [ctx.add_image(f) for f in ctx.frames(float(t0), float(t1), max(1, min(int(n), 4)))]
    return ToolResult(f"{len(refs)} frames between {t0:.1f} and {t1:.1f}", refs)


def crop_zoom(ctx: ToolContext, track_id: int, ts: float) -> ToolResult:
    data = ctx.crops(int(track_id), float(ts))
    if not data:
        return ToolResult("no crop for that track at that time")
    img = _decode(data)
    big = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return ToolResult("zoomed crop", [ctx.add_image(_encode(_sharpen(big)))])


def enhance(ctx: ToolContext, image: str, kind: str = "brighten") -> ToolResult:
    if image not in ctx.images:
        return ToolResult(f"no image called {image}")
    img = _decode(ctx.images[image])
    if kind == "brighten":
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        lab[..., 0] = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(lab[..., 0])
        out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    elif kind == "denoise":
        out = cv2.fastNlMeansDenoisingColored(img, None, 7, 7, 7, 21)
    else:
        out = _sharpen(img)
    return ToolResult(f"{kind} applied to {image}", [ctx.add_image(_encode(out))])


def motion_trail(ctx: ToolContext, track_id: int) -> ToolResult:
    path = ctx.boxes(int(track_id))
    if len(path) < 2:
        return ToolResult("not enough track history")
    frames = ctx.frames(path[-1][0], path[-1][0], 1)
    if not frames:
        return ToolResult("no frame to draw on")
    img = _decode(frames[0])
    pts = np.array([[b[0] + b[2] / 2, b[1] + b[3]] for _, b in path], np.int32)
    cv2.polylines(img, [pts], False, (0, 220, 255), 2, cv2.LINE_AA)
    cv2.circle(img, tuple(pts[-1]), 5, (0, 0, 255), -1)
    seconds = path[-1][0] - path[0][0]
    return ToolResult(f"path over {seconds:.0f} s drawn in yellow, last position red", [ctx.add_image(_encode(img))])


def flow(ctx: ToolContext, track_id: int, t0: float, t1: float) -> ToolResult:
    frames = ctx.frames(float(t0), float(t1), 2)
    path = ctx.boxes(int(track_id))
    if len(frames) < 2 or not path or t1 <= t0:
        return ToolResult("not enough frames to measure motion")
    a, b = (cv2.cvtColor(_decode(f), cv2.COLOR_BGR2GRAY) for f in frames[:2])
    field_ = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST).calc(a, b, None)
    x, y, w, h = (int(v) for v in path[-1][1])
    region = field_[max(0, y):y + h, max(0, x):x + w]
    if region.size == 0:
        return ToolResult("the track is outside the frame")
    down = float(np.median(region[..., 1])) / max(h, 1) / (t1 - t0)
    side = float(np.median(region[..., 0])) / max(h, 1) / (t1 - t0)
    return ToolResult(f"median motion inside the box: {down:+.2f} body heights per second downward, "
                      f"{side:+.2f} sideways")


def pose_history(ctx: ToolContext, track_id: int, seconds: float = 6) -> ToolResult:
    poses = ctx.poses(int(track_id), min(float(seconds), 10.0))
    if not poses:
        return ToolResult("no pose estimates for that track")
    return ToolResult("; ".join(f"t={p['ts']:.1f} torso angle {p.get('torso_angle', 0):.0f}" for p in poses))


TOOLS: dict[str, tuple[str, Callable]] = {
    "get_frames": ("get_frames(t0, t1, n): up to 4 frames between two times", get_frames),
    "crop_zoom": ("crop_zoom(track_id, ts): a sharpened 2x zoom of a tracked object", crop_zoom),
    "enhance": ("enhance(image, kind): brighten, denoise or sharpen an image you have, by its name like img1",
                enhance),
    "motion_trail": ("motion_trail(track_id): the path the object walked, drawn on the latest frame", motion_trail),
    "flow": ("flow(track_id, t0, t1): optical flow inside the box, how fast it moves down and sideways", flow),
    "pose_history": ("pose_history(track_id, seconds): torso angles over time (0 upright, 90 lying)", pose_history),
}
TERMINAL = {
    "raise_alert": "raise_alert(summary): it is real; tell the owner",
    "dismiss": "dismiss(reason): it is not what the watch asks for",
    "ask_human": "ask_human(question): a person should decide before anyone is contacted",
}


def describe_tools() -> str:
    return "\n".join(f"* {d}" for d, _ in TOOLS.values()) + "\n" + "\n".join(f"* {d}" for d in TERMINAL.values())


def run_tool(ctx: ToolContext, name: str, args: dict) -> ToolResult:
    if name not in TOOLS:
        return ToolResult(f"unknown tool {name}")
    try:
        return TOOLS[name][1](ctx, **args)
    except (TypeError, ValueError, cv2.error) as e:
        return ToolResult(f"tool failed: {e}")
