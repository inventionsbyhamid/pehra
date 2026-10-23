import cv2
import numpy as np

from watchful.evidence import ZONE_COLOR, EvidencePack, evidence_context
from watchful.rules.engine import Candidate


def test_frames_for_the_ai_show_zones_and_the_box():
    gray = cv2.imencode(".jpg", np.full((200, 400, 3), 90, np.uint8))[1].tobytes()
    c = Candidate("w", "cam", 50.0, 3, "info", {}, [], "fp")
    pack = EvidencePack(c, [(49.0, gray), (50.0, gray)], [(50.0, (300.0, 50.0, 40.0, 100.0))],
                        zones=[("door", [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)])])
    ctx = evidence_context(pack, None, None)
    assert ctx.moment == 50.0 and ctx.track_id == 3
    img = cv2.imdecode(np.frombuffer(ctx.frames(49.5, 50.5, 1)[0], np.uint8), cv2.IMREAD_COLOR)
    edge = img[100, 198:203].astype(int)
    assert np.abs(edge - np.array(ZONE_COLOR)).sum(axis=1).min() < 120  # the zone's right edge at x = 200
    assert np.abs(img[100, 300].astype(int) - 90).sum() > 60  # the box's left edge is drawn
