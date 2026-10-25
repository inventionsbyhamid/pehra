"""Every threshold in one place. Defaults here; override with TUNING_FILE and per camera."""
import json
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    # run the detector on every Nth analysed frame while only already tracked things move;
    # the tracker predicts in between and new motion anywhere else forces a detection
    stride: int = 2
    known_motion_overlap: float = 0.5  # share of a motion blob inside a track box that counts as known


class TrackTuning(_Section):
    iou: float = 0.3
    max_age_s: float = 2.0
    min_hits: int = 2
    high_score: float = 0.5


class PoseTuning(_Section):
    conf: float = 0.5
    enlarge: float = 1.25
    rate_hz: float = 2.0
    # for fall watches, pose runs only while a person's box looks like a fall may be happening
    suspicious_aspect: float = 0.6
    suspicious_drop: float = 0.3  # box centre drop, box heights per second
    suspicious_hold_s: float = 4.0


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
    box_drop_speed: float = 0.4  # box centre drop, box heights per second
    box_aspect_rise: float = 0.5
    history_s: float = 3.0


class FaceTuning(_Section):
    cosine: float = 0.363  # SFace's documented threshold
    detect_score: float = 0.7
    region: float = 0.4  # top share of a person box searched for a face
    rate_hz: float = 1.0


class AppearanceTuning(_Section):
    same: float = 0.35  # Bhattacharyya distance under which clothes match
    continuity_s: float = 600.0  # a face named group takes look alike tracks this close in time
    carry_name: float = 0.25  # stricter clothing distance for carrying a recognised name to a track with no face
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
    covered_std: float = 8
    covered_ratio: float = 0.3  # and below this share of the reference view's contrast
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
    looks_like_threshold: float = 0.1  # cosine, for embedders without a calibration
    looks_like_probability: float = 0.05  # match probability for a described subject
    search_min_probability: float = 0.01
    same_as_threshold: float = 0.8
    zone_margin: float = 0.02
    contrast_margin: float = 0.01  # how much closer to text than to not_text an object must look
    zone_embed_every_s: float = 3.0
    search_top_n: int = 12  # best matches the AI double checks
    search_dedupe_s: float = 10.0  # scene matches closer than this on one camera count once
    search_embed_on_demand: int = 24  # unindexed moments embedded during a search; the rest wait for the indexer
    alone_distance: float = 3.0  # body heights: closer than this many of the smaller box is always company, and up to
    alone_depth_ratio: float = 1.6  # this many of the mean box is company unless heights this far apart (other depth)
    alert_cooldown_floor_s: float = 5.0  # an alert watch never fires again for the same thing sooner than this
    stationary_move: float = 0.25  # box heights the centre may wander and still count as staying put
    parked_rejoin_s: float = 60.0  # a new track on a parked spot within this long takes over its clock
    parked_rejoin_iou: float = 0.5
    parked_template_match: float = 0.5  # edge picture correlation that says the parked thing is still there
    still_every_s: float = 0.5
    still_flow: float = 0.04  # body heights a second of own movement under which it is still (swimmers about 0.1)
    still_drift: float = 0.05  # body heights per second the box centre may drift and still be still
    still_percentile: float = 75  # of the own movement inside the box, so moving arms or legs count
    still_pad: float = 0.5  # share of the box added around it: the ring whose flow is the water or ground
    still_width: int = 160  # crops are resized to this before the flow; small crops blur body and water together
    still_preset: Literal["ultrafast", "fast", "medium"] = "medium"  # DIS; medium about 8 ms a look on the dev box
    still_gap_s: float = 3.0  # a longer gap between checks starts the still clock again
    vanish_hold_s: float = 30.0  # seconds out of sight before a vanish counts, unless the watch says
    vanish_hold_by_kind: dict[str, float] = {"water": 15.0}
    vanish_near: float = 2.0  # body heights; someone appearing this close to the spot is the same one back
    vanish_edge: float = 0.05  # share of the frame; tracks ending this close to an edge walked out of view


class ZoneTuning(_Section):
    slow_tick_s: float = Field(5.0, ge=2, le=10)  # zone checks that run even when nothing moves
    line_hysteresis: float = 0.1  # share of the box height a foot point must clear a line by to change side
    line_extend: float = 0.1  # share of a line's length a crossing may land beyond either end
    line_max_jump: float = 0.5  # box sizes; a bigger jump onto where another track just stood is an id swap
    line_swap_near: float = 0.5  # box sizes from another track's last foot point that counts as its spot
    state_size: int = 64  # zone pictures are compared at this size
    state_pre_blur: float = 1.0  # before CLAHE, so it does not lift sensor noise into edges
    state_blur: float = 2.0  # after, so a gate a few pixels off still matches
    state_clahe_clip: float = 2.0
    state_clahe_tiles: int = 4
    state_edge_weight: float = 0.5  # edge picture against the image model, when there is one
    state_margin: float = 0.05  # how much nearer one state's examples must be than the next state's
    state_min_score: float = 0.3
    state_unsure_checks: int = 3  # this many unsure looks in a row and the state is unknown
    state_max_age_s: float = 120.0  # a state not confirmed for this long is unknown
    night_saturation: float = 20  # mean saturation of a small copy under which a picture is night (IR)
    night_probe_width: int = 32


class BacktestTuning(_Section):
    step_s: float = 1.0  # replay ticks between events while something is in view
    page_s: float = 3600.0
    page_limit: int = 5000
    min_page_s: float = 60.0
    max_examples: int = 5
    max_track_s: float = 3600.0  # a track with no end in the index is closed after this
    max_seconds: float = 10.0  # a backtest stops after this much work and says how far it got


class PerceiveTuning(_Section):
    keyframe_every_s: float = 2.0
    scene_every_s: float = 3.0  # a whole frame for the search index while something moves
    scene_idle_every_s: float = 60.0  # and now and then when nothing does
    embed_every_s: float = 3.0  # people's current crop, while a watch or search needs embeddings (local, no AI)
    max_embeds_per_track: int = 0  # 0 means no limit
    jpeg_quality: int = 80


class EvidenceTuning(_Section):
    before_s: float = 3.0
    after_s: float = 1.5  # a candidate waits this long so the AI also sees what came next
    fps: float = 2.0
    width: int = 640


class ScanTuning(_Section):
    min_chunk_s: float = 4.0
    target_chunk_s: float = 15.0  # long videos are cut finer than one chunk per worker, for load balance
    decode_threads: int = 1  # each chunk worker decodes on one thread; the chunks already fill the CPUs
    join_iou: float = 0.5  # mean box overlap in the shared seconds that joins two tracks
    join_distance: float = 0.5  # fallback: centre distance in box heights between last and first boxes
    join_gap_s: float = 1.0


class ClipTuning(_Section):
    before_s: float = 5.0
    after_s: float = 5.0
    crf: int = 28
    preset: str = "veryfast"
    threads: int = 1  # x264 threads per clip; on a many core host its default takes about 170 MB a clip
    writers: int = 2  # clips a camera process encodes at once; the rest wait as JPEGs
    nice: int = 10  # clips encode at a lower priority than the analysis, which must keep its frame rate


class AlertTuning(_Section):
    group_s: float = 600.0  # a trigger this soon after the last one on the same camera and watch joins its incident
    # after a false alarm tap, that very trigger stays quiet this long from the tap (never longer); a new one is checked
    false_alarm_quiet_s: float = 300.0
    fingerprints_kept: int = 50  # triggers an incident remembers, for false_alarm_quiet_s
    escalate_min: float = Field(2.0, ge=0.25)  # an unacknowledged critical incident is posted again after this long
    hold_max_s: float = 15.0  # an incident held for its AI check goes out as not checked yet when no verdict comes
    escalate_times: int = 3  # and at most this many times
    edit_every_s: float = 10.0  # a count change edits a Telegram message at most this often; a status change at once
    chat_per_min: int = 18  # Telegram allows about 20 messages a minute in a group
    chat_gap_s: float = 1.0  # and about one a second in one chat
    chat_retry_s: float = 15.0  # a group whose message failed is tried again this soon; the other groups go on
    chat_tries: int = 5  # then it is given up for that message
    code_minutes: float = 30.0  # a Telegram connect code works this long, once
    code_length: int = 8
    summary_hour: int = 9  # the daily summary, in the society's time zone
    summary_late_h: float = 3.0  # a worker that was down at summary_hour still sends it this many hours late, no later
    faces_refresh_s: float = 60.0  # enrolled faces used for blurring are read again this often
    http_timeout_s: float = 15.0
    bot_retry_s: float = 300.0  # the app asks Telegram for the bot's name again this long after a failed try


class CropTuning(_Section):
    margin: float = 0.6  # share of the box's width and height added on each side of a still
    max_side: int = 960
    detect_side: int = 640  # stills smaller than this are enlarged for the face detector, so small faces are found
    face_score: float = 0.5  # lower than enrolment's, so in doubt a face is blurred
    face_grow: float = 0.35  # share of a face box added around it before blurring
    blur_cells: int = 6  # a face is pixelated to this many cells across, then smoothed
    # a person box with no face found: the head is placed from pose landmarks (nose, eyes, ears, mouth) seen at least
    # this surely, as a square head_grow times their spread and at least head_min of the box's longer side; with no
    # landmarks the whole box is blurred
    pose_visible: float = 0.5
    head_grow: float = 3.0
    head_min: float = 0.2
    person_overlap: float = 0.5  # a person the detector finds overlapping a given person box this much is that person
    jpeg_quality: int = 85


class JobTuning(_Section):
    lease_s: float = 300.0
    urgent_lease_s: float = 30.0  # an urgent job (a Telegram send) left by a stopped worker runs again this soon
    max_attempts: int = 5
    retry_in_s: float = 60.0
    urgent_retry_s: float = 5.0  # a failed urgent job (a Telegram send) is tried again this soon
    idle_s: float = 1.0
    urgent_idle_s: float = 0.2
    error_wait_s: float = 5.0
    daily_check_s: float = 600.0
    periodic_s: float = 60.0  # how often a running worker queues timed jobs, such as the 9 AM summaries
    worker_alive_s: float = 60.0  # a worker that has not beaten for this long is taken as stopped
    heartbeat_s: float = 60.0  # worker_up is put this often
    keep_done_days: float = 7.0  # finished jobs are deleted after this many days


class PlateTuning(_Section):
    max_crops: int = 5
    detect_side: int = 640  # LPD-YuNet input, longest side, rounded to 64
    detect_score: float = 0.5
    detect_nms: float = 0.3
    max_plates: int = 3  # plate boxes tried per crop, best first
    outside: float = 0.05  # a plate box may cross the crop's edge by this share of it, no more
    plate_aspect: tuple[float, float] = (0.8, 8.0)  # width over height of a plausible plate box
    min_fill: float = 0.5  # a plate quad covering less of its bounding box has wrong corners: the box is used
    margin: float = 0.08  # plate quad grown by this share before warping
    plate_height: int = 96  # warped plate height before its lines are cut
    two_line_aspect: float = 2.6  # plates narrower than this (width over height) may hold two lines
    split_band: tuple[float, float] = (0.3, 0.7)  # where the gap between two lines may be, share of height
    split_gap: float = 0.35  # rows with less text than this share of the busy rows are a gap
    split_side: float = 0.1  # share of the plate width left out on each side when counting text rows
    group_overlap: float = 0.5  # text boxes overlapping this share of the shorter height share a row
    region_margin: float = 0.3  # text rows grown by this share to judge the plate around them
    reread_margin: float = 0.6  # a plate box whose read needed fixes is grown by this share and read as text rows
    busy_percentile: float = 90  # the busy rows a gap is measured against
    max_fixes: int = 2  # characters the grammar may change in one read
    drop_cost: float = 1.5  # a character left out around the plate text, against one changed
    rank_cost: float = 0.3  # each step away from the most common layout (data/plates.json lengths order)
    max_cost: float = 2.0  # reads costing more than this are not plates
    unscored_read: float = 0.5  # how sure a recogniser that gives no score (CRNN) counts
    cost_conf: float = 0.3  # confidence lost per unit of grammar cost (a changed character is 1)
    box_floor: float = 0.5  # confidence kept by a read whose plate box scored 0: floor + (1 - floor) * box score
    rows_score: float = 0.7  # the box score of a read from PP-OCR text rows with no plate box around them
    min_share: float = 0.6  # share of the reads' confidence the voted text needs
    min_char_votes: int = 3  # reads of one length needed to vote character by character
    unread_weight: float = 0.15  # an unread crop counts against the voted text as a read this sure
    single_read: float = 0.8  # a text only one crop read keeps this share of its confidence
    min_conf: float = 0.52  # a voted text less sure than this is left unread (the guess stays in reads)
    grey_chroma: float = 12  # max minus min channel at grey_percentile under this: an IR frame
    grey_percentile: float = 99
    dark_v: float = 60  # the brighter of a plate's background and text under this: too dark to judge
    min_contrast: float = 40  # background and text closer than this in V: too flat to judge
    colour_border: float = 0.12  # plate edge share left out when judging its colour
    colour_s: float = 70  # background saturation from which a plate is coloured, not white
    white_v: float = 110
    black_v: float = 80
    yellow_hue: tuple[float, float] = (12, 40)  # OpenCV hue, 0 to 180
    green_hue: tuple[float, float] = (40, 90)


class AuthTuning(_Section):
    session_days: int = 30  # a sign in lasts this many days in a browser
    sign_in_minutes: int = 10  # time allowed between leaving for the sign in provider and coming back
    invite_days: int = 7  # an invite link works this long, and only once
    signin_link_minutes: int = 15  # an operator's sign in link (python -m watchful.auth) works this long, and once
    media_link_s: int = 3600  # signed links to pictures and clips (/api/blobs) work this long


class LimitsTuning(_Section):
    login_per_min_ip: int = 10  # sign in attempts a minute per IP (Google, operator links); IPv6 per /64
    api_per_min_workspace: int = 600  # signed in API requests a minute per society (several people, pages polling)
    public_per_min_ip: int = 120  # pages behind a valid link a minute per IP
    misses_per_min_ip: int = 10  # unknown sign in link tokens a minute per IP: guessing
    grace_days: int = 5  # a society keeps its plan this many days after a failed renewal
    webhook_ids_days: int = 90  # handled billing webhook ids are kept this long, to drop retries
    webhook_kb: int = 512  # largest billing webhook body
    billing_link_s: int = 3600  # a test billing link (the fake provider) works this long
    billing_api_timeout_s: float = 10.0  # one request to the billing provider's API
    plan_check_s: float = 300.0  # how often the app switches off cameras a plan no longer covers
    refund_orders_margin_s: float = 300.0  # a refund takes the subscription's payments from this long before its year
    billing_poll_s: float = 2.0  # the billing panel asks again this often while a payment or change is confirmed
    billing_poll_tries: int = 15  # and at most this many times


class PushTuning(_Section):
    """Cameras that send their own frames: a browser or phone at /cam/{token}, or the bridge (bridge/)."""
    fps: float = 5.0  # frames a second a device sends, the analysis rate
    width: int = 960  # a device sends frames at most this wide
    quality: float = 0.72  # JPEG quality a device encodes with, 0 to 1
    max_frame_kb: int = 1024  # a bigger frame is dropped
    max_fps: float = 12.0  # frames arriving faster than this are dropped
    recheck_s: float = 15.0  # an open socket checks its device token or membership again this often
    face_width: int = 320  # the browser looks for faces (YuNet in OpenCV.js) on a copy this wide
    face_score: float = 0.5  # a face found this surely or more is blurred
    face_grow: float = 0.3  # the blurred box reaches this share of the face's size beyond it on each side
    face_hold_ms: int = 1200  # a face stays blurred this long after it was last found
    face_blocks: int = 6  # the mosaic over a face is this many blocks across
    face_failures: int = 3  # the page stops, saying why, after this many frames in a row it could not blur


class HealthTuning(_Section):
    offline_s: float = 30.0  # no picture for this long and the camera is offline
    start_s: float = 60.0  # a camera is not judged until its process reports it, for at most this long
    check_every_s: float = 1.0
    seen_every_s: float = 30.0  # a running camera's last picture time is written to the database this often


class ResourcesTuning(_Section):
    # RSS of the app, its camera and scan processes and the job worker together, which deploy/check_memory.py checks:
    # c8g.large has 4 GiB, the rest is for the OS, Caddy and Litestream
    memory_budget_mb: int = 3072
    latency_window: int = 300  # analysed frames per camera behind its latency percentiles
    restart_max_s: float = 300.0  # longest wait before a dead camera process is started again
    text_tower_idle_s: float = 300.0  # the app drops the image model's text tower (about 1.5 GB) after this long unused
    report_every_s: float = 1.0  # camera status to the app


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
    perceive: PerceiveTuning = PerceiveTuning()
    scan: ScanTuning = ScanTuning()
    clips: ClipTuning = ClipTuning()
    alerts: AlertTuning = AlertTuning()
    crops: CropTuning = CropTuning()
    jobs: JobTuning = JobTuning()
    evidence: EvidenceTuning = EvidenceTuning()
    plates: PlateTuning = PlateTuning()
    zones: ZoneTuning = ZoneTuning()
    backtest: BacktestTuning = BacktestTuning()
    auth: AuthTuning = AuthTuning()
    limits: LimitsTuning = LimitsTuning()
    push: PushTuning = PushTuning()
    health: HealthTuning = HealthTuning()
    resources: ResourcesTuning = ResourcesTuning()


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
