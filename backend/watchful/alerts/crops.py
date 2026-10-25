"""Stills for alerts that leave the app (Telegram): the moment around the flagged box, with the face of everyone who
is not an enrolled, consenting staff member blurred. YuNet finds the faces and SFace says whether a face is enrolled
(the society's FaceIndex); in doubt a face is blurred. A person whose face cannot be found has the head blurred where
MediaPipe pose puts it, or the whole person when pose cannot tell."""
import threading

import cv2
import numpy as np

from watchful.tuning import CropTuning

Box = tuple[float, float, float, float]  # x, y, w, h in the frame's pixels
HEAD_LANDMARKS = 11  # MediaPipe pose: nose, eyes (inner, centre, outer), ears, mouth corners


def _region(shape, box: Box | None, margin: float) -> tuple[int, int, int, int]:
    h, w = shape[:2]
    if box is None:
        return 0, 0, w, h
    x, y, bw, bh = box
    x0, y0 = int(max(0, x - margin * bw)), int(max(0, y - margin * bh))
    x1, y1 = int(min(w, x + bw + margin * bw)), int(min(h, y + bh + margin * bh))
    return (x0, y0, x1, y1) if x1 - x0 >= 2 and y1 - y0 >= 2 else (0, 0, w, h)


def _blur(image: np.ndarray, x0: int, y0: int, x1: int, y1: int, cells: int) -> None:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    patch = image[y0:y1, x0:x1]
    across = max(1, cells)
    down = max(1, round(across * (y1 - y0) / (x1 - x0)))
    small = cv2.resize(patch, (across, down), interpolation=cv2.INTER_AREA)
    blocky = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)
    k = max(3, ((min(x1 - x0, y1 - y0) // across) | 1))
    image[y0:y1, x0:x1] = cv2.GaussianBlur(blocky, (k, k), 0)


def _faces(image: np.ndarray, index, cfg: CropTuning) -> list[tuple[Box, bool]]:
    """Each face in the image (its pixels) and whether it is an enrolled person's."""
    h, w = image.shape[:2]
    scale = max(1.0, cfg.detect_side / max(h, w))
    probe = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_CUBIC) \
        if scale > 1.0 else image
    detector, recognizer = index.detector, index.recognizer
    detector.setInputSize((probe.shape[1], probe.shape[0]))
    before = detector.getScoreThreshold()
    detector.setScoreThreshold(min(before, cfg.face_score))
    try:
        _, found = detector.detect(probe)
    finally:
        detector.setScoreThreshold(before)
    people = [np.asarray(e.feature, np.float32).reshape(1, -1) for e in index.people()]
    out = []
    for face in [] if found is None else found:
        known = False
        if people:
            feature = recognizer.feature(recognizer.alignCrop(probe, face))
            known = any(recognizer.match(feature, ref, cv2.FaceRecognizerSF_FR_COSINE) >= index.threshold
                        for ref in people)
        out.append(((face[0] / scale, face[1] / scale, face[2] / scale, face[3] / scale), known))
    return out


def _iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _pose_head(image: np.ndarray, box: Box, pose, cfg: CropTuning) -> tuple[float, float, float, float] | None:
    """Where a person's head is from pose landmarks (nose, eyes, ears, mouth), lying or upright; None when the pose
    model finds too few of them. A wide box is tried turned (the pose model's own way) and upright (as a square box
    around it), keeping the surer result: someone running with arms out is wide but upright."""
    if pose is None:
        return None
    x, y, w, h = box
    side = max(w, h)
    tries = [box] + ([(x + w / 2 - side / 2, y + h / 2 - side / 2, side, side)] if w != h else [])
    results = []
    for b in tries:
        try:
            r = pose.estimate(image, b)
        except cv2.error:
            r = None
        if r is not None and r.landmarks is not None:
            results.append(r)
    if not results:
        return None
    found = max(results, key=lambda r: r.confidence)
    head = found.landmarks[:HEAD_LANDMARKS]
    seen = head[head[:, 3] >= cfg.pose_visible, :2]
    if len(seen) < 2:
        return None
    cx, cy = seen.mean(axis=0)
    spread = max(float(np.ptp(seen[:, 0])), float(np.ptp(seen[:, 1])), 1.0)
    half = max(spread * cfg.head_grow, cfg.head_min * max(box[2], box[3])) / 2
    return cx - half, cy - half, cx + half, cy + half


def blurred_crop(frame: np.ndarray, box: Box | None, enrolled_faces, persons: list[Box] = (),
                 cfg: CropTuning = CropTuning(), pose=None, detector=None) -> bytes:
    """A JPEG of the frame around `box` (the whole frame when None), every face blurred unless `enrolled_faces` (a
    FaceIndex loaded with the society's enrolled staff) recognises it. Each person, from `persons` (boxes in the
    frame's pixels) and from the person `detector` run on the still, whose face is not found at all has the head
    blurred where `pose` places it, or the whole box when it cannot: a face from above, in profile, small, lying or
    under infrared light is often not found, and in doubt nothing is left sharp."""
    x0, y0, x1, y1 = _region(frame.shape, box, cfg.margin)
    image = frame[y0:y1, x0:x1].copy()
    k = min(1.0, cfg.max_side / max(image.shape[:2]))
    if k < 1.0:
        image = cv2.resize(image, (round(image.shape[1] * k), round(image.shape[0] * k)), interpolation=cv2.INTER_AREA)
    faces = _faces(image, enrolled_faces, cfg)
    people = [((px - x0) * k, (py - y0) * k, pw * k, ph * k) for px, py, pw, ph in persons]
    if detector is not None:
        for d in detector.detect(image):
            if d.cls == "person" and all(_iou(d.box, p) < cfg.person_overlap for p in people):
                people.append(d.box)
    original = image.copy()  # pose reads the picture before any blur
    for (fx, fy, fw, fh), known in faces:
        if not known:
            gx, gy = cfg.face_grow * fw, cfg.face_grow * fh
            _blur(image, int(fx - gx), int(fy - gy), int(fx + fw + gx), int(fy + fh + gy), cfg.blur_cells)
    for px, py, pw, ph in people:
        if any(px <= fx + fw / 2 <= px + pw and py <= fy + fh / 2 <= py + ph for (fx, fy, fw, fh), _ in faces):
            continue  # its face was found: blurred above, or an enrolled one kept
        head = _pose_head(original, (px, py, pw, ph), pose, cfg) or (px, py, px + pw, py + ph)
        _blur(image, *(int(v) for v in head), cfg.blur_cells)
    ok, jpg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, cfg.jpeg_quality])
    if not ok:
        raise ValueError("the still could not be encoded")
    return jpg.tobytes()


def frame_still(jpeg: bytes, box: Box | None, cfg: CropTuning = CropTuning()) -> tuple[bytes, list[float] | None]:
    """A private copy of the moment for an incident: the frame around the box, unblurred (it never leaves the app;
    blurred_crop cuts Telegram's still from it), and the box in the copy's pixels."""
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("not a picture")
    x0, y0, x1, y1 = _region(image.shape, box, 2 * cfg.margin)
    ok, jpg = cv2.imencode(".jpg", image[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, cfg.jpeg_quality])
    if not ok:
        raise ValueError("the still could not be encoded")
    inner = None if box is None else [round(box[0] - x0, 1), round(box[1] - y0, 1), round(box[2], 1), round(box[3], 1)]
    return jpg.tobytes(), inner


class StillMaker:
    """One face detector and recognizer, pose model and person detector for the whole process (memory must not grow
    with the number of societies), used by one still at a time, since a still changes the face detector's input size
    and threshold; each society brings its own enrolled faces."""

    def __init__(self, index, pose=None, detector=None):
        self.index, self.pose, self.detector, self._lock = index, pose, detector, threading.Lock()

    def make(self, frame: np.ndarray, box: Box | None, enrolled: list, persons: list[Box] = (),
             cfg: CropTuning = CropTuning()) -> bytes:
        with self._lock:
            self.index.load(enrolled)
            return blurred_crop(frame, box, self.index, persons, cfg, self.pose, self.detector)
