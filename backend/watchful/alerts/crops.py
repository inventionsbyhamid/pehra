"""Stills for alerts that leave the app (Telegram): the moment around the flagged box, with the face of everyone who
is not an enrolled, consenting staff member blurred. YuNet finds the faces and SFace says whether a face is enrolled
(the society's FaceIndex); in doubt a face is blurred. A person box whose face cannot be found has its head blurred."""
import threading

import cv2
import numpy as np

from watchful.tuning import CropTuning

Box = tuple[float, float, float, float]  # x, y, w, h in the frame's pixels


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


def blurred_crop(frame: np.ndarray, box: Box | None, enrolled_faces, heads: list[Box] = (),
                 cfg: CropTuning = CropTuning()) -> bytes:
    """A JPEG of the frame around `box` (the whole frame when None), every face blurred unless `enrolled_faces` (a
    FaceIndex loaded with the society's enrolled staff) recognises it, and the head of each person box in `heads`
    blurred unless an enrolled face is in it."""
    x0, y0, x1, y1 = _region(frame.shape, box, cfg.margin)
    image = frame[y0:y1, x0:x1].copy()
    k = min(1.0, cfg.max_side / max(image.shape[:2]))
    if k < 1.0:
        image = cv2.resize(image, (round(image.shape[1] * k), round(image.shape[0] * k)), interpolation=cv2.INTER_AREA)
    faces = _faces(image, enrolled_faces, cfg)
    for (fx, fy, fw, fh), known in faces:
        if not known:
            gx, gy = cfg.face_grow * fw, cfg.face_grow * fh
            _blur(image, int(fx - gx), int(fy - gy), int(fx + fw + gx), int(fy + fh + gy), cfg.blur_cells)
    for hx, hy, hw, hh in heads:
        hx, hy, hw, hh = (hx - x0) * k, (hy - y0) * k, hw * k, hh * cfg.head_share * k
        enrolled_here = any(known and hx <= fx + fw / 2 <= hx + hw and hy <= fy + fh / 2 <= hy + hh
                            for (fx, fy, fw, fh), known in faces)
        if not enrolled_here:
            _blur(image, int(hx), int(hy), int(hx + hw), int(hy + hh), cfg.blur_cells)
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
    """One face detector and recognizer for the whole process (memory must not grow with the number of societies),
    used by one still at a time, since a still changes the detector's input size and threshold; each society brings
    its own enrolled faces."""

    def __init__(self, index):
        self.index, self._lock = index, threading.Lock()

    def make(self, frame: np.ndarray, box: Box | None, people: list, heads: list[Box] = (),
             cfg: CropTuning = CropTuning()) -> bytes:
        with self._lock:
            self.index.load(people)
            return blurred_crop(frame, box, self.index, heads, cfg)
