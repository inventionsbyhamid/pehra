"""Every threshold in one place. Defaults here; override with TUNING_FILE and per camera."""
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MotionTuning(_Section):
    history: int = 500
    var_threshold: float = 25
    min_ratio: float = 0.002  # share of changed pixels that counts as motion
    min_box_ratio: float = 0.001  # smallest blob, as a share of the frame
    gate_width: int = 320


class DetectTuning(_Section):
    conf: float = 0.4
    nms: float = 0.45


class TrackTuning(_Section):
    iou: float = 0.3
    max_age_s: float = 2.0
    min_hits: int = 2
    high_score: float = 0.5


class PoseTuning(_Section):
    conf: float = 0.5
    enlarge: float = 1.25
    rate_hz: float = 2.0


class FallTuning(_Section):
    drop_speed: float = 0.6  # body heights per second
    angle_rise: float = 30.0  # degrees within window_s
    window_s: float = 1.0
    lying_angle: float = 60.0
    lying_aspect: float = 1.3
    hold_s: float = 2.0
    up_angle: float = 35.0
    up_aspect: float = 1.0
    box_only_confidence: float = 0.5
    history_s: float = 3.0


class FaceTuning(_Section):
    cosine: float = 0.363  # SFace's documented threshold
    detect_score: float = 0.7
    region: float = 0.4  # top share of a person box searched for a face
    rate_hz: float = 1.0


class AppearanceTuning(_Section):
    same: float = 0.35  # Bhattacharyya distance under which clothes match
    continuity_s: float = 600.0  # a face named group takes look alike tracks this close in time
    torso_top: float = 0.2
    torso_bottom: float = 0.6
    torso_side: float = 0.2


class QualityTuning(_Section):
    dark_v: float = 60
    clahe_clip: float = 2.0
    clahe_tiles: int = 8


class TamperTuning(_Section):
    check_every_s: float = 2.0
    hold_s: float = 5.0
    covered_mean: float = 15
    covered_std: float = 8
    blur_ratio: float = 0.2
    orb_features: int = 500
    ratio_test: float = 0.75
    min_matches: int = 10
    min_inlier_share: float = 0.25
    max_shift: float = 0.05  # share of the frame width
    reference_refresh_s: float = 300
    width: int = 480


class SignalTuning(_Section):
    climb_rise: float = 0.5
    climb_window_s: float = 2.0
    looks_like_threshold: float = 0.1
    same_as_threshold: float = 0.8
    zone_margin: float = 0.02
    zone_embed_every_s: float = 3.0


class ScanTuning(_Section):
    min_chunk_s: float = 4.0
    clip_before_s: float = 5.0
    clip_after_s: float = 5.0


class Tuning(_Section):
    motion: MotionTuning = MotionTuning()
    detect: DetectTuning = DetectTuning()
    track: TrackTuning = TrackTuning()
    pose: PoseTuning = PoseTuning()
    falls: FallTuning = FallTuning()
    faces: FaceTuning = FaceTuning()
    appearance: AppearanceTuning = AppearanceTuning()
    quality: QualityTuning = QualityTuning()
    tamper: TamperTuning = TamperTuning()
    signals: SignalTuning = SignalTuning()
    scan: ScanTuning = ScanTuning()


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load_tuning(path: Path | None = None, overrides: dict | None = None) -> Tuning:
    data = Tuning().model_dump()
    if path:
        data = _merge(data, json.loads(Path(path).read_text()))
    if overrides:
        data = _merge(data, overrides)
    return Tuning.model_validate(data)
