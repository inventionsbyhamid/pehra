"""Privacy fix round leftovers (Task 7 round 3, Task 4 minors): every person's head is blurred in a still that leaves
the app unless their own head holds an enrolled face; verdicts the AI could not settle stay quiet after a person's
false alarm; token paths in uvicorn's access log keep the line. The egress units are checked in test_netguard and the
device's hello in web/e2e/cam.spec.ts."""
import logging
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from test_incidents import RAVI, at, env, live  # noqa: F401  (env is a fixture)
from test_telegram import found_faces, sharpness
from watchful.agent.verifier import Verdict
from watchful.alerts import incidents as inc_
from watchful.alerts.crops import blurred_crop
from watchful.config import Settings
from watchful.rules.engine import Candidate
from watchful.tuning import CropTuning, Tuning
from watchful.vision.faces import FaceIndex
from watchful.vision.models import sample_path


# ---- F: every person's head is blurred, unless their own head holds an enrolled face ----

class _Faces:
    """A face detector and recognizer that find what the test says: (box, enrolled) pairs."""

    def __init__(self, faces):
        rows = [[x, y, w, h] + [x + w / 2, y + h / 2] * 5 + [0.99] for (x, y, w, h), _ in faces]
        self.found = np.array(rows, np.float32) if rows else None
        self.known = [k for _, k in faces]
        self.detector, self.recognizer, self.threshold = self, self, 0.363
        self._probe = None

    def setInputSize(self, size):
        pass

    def getScoreThreshold(self):
        return 0.7

    def setScoreThreshold(self, value):
        pass

    def detect(self, image):
        return 1, self.found

    def people(self):
        return [SimpleNamespace(feature=[1.0] * 128)] if any(self.known) else []

    def alignCrop(self, image, face):
        return np.int32([i for i, row in enumerate(self.found) if np.array_equal(row, face)][0])

    def feature(self, i):
        return i

    def match(self, i, ref, kind):
        return 1.0 if self.known[int(i)] else 0.0


@pytest.fixture(scope="module")
def messi():
    from watchful.components import make_detector, make_pose
    settings, tuning = Settings(_env_file=None, data_key="k" * 32), Tuning()
    frame = cv2.imread(str(sample_path("messi5.jpg")))
    people = [d for d in make_detector(settings, tuning, {"person"}).detect(frame) if d.cls == "person"]
    box = tuple(float(v) for v in max(people, key=lambda d: d.box[2] * d.box[3]).box)
    face = max(found_faces(frame), key=lambda f: f[2] * f[3])
    return SimpleNamespace(frame=frame, box=box, face=face, pose=make_pose(settings, tuning))


def _still(m, faces, persons=None, detect_side=640):
    cfg = CropTuning(max_side=4000, detect_side=detect_side)
    out = blurred_crop(m.frame, None, faces, [m.box] if persons is None else persons, cfg, m.pose)
    return cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)


def test_f_someone_elses_face_in_the_box_does_not_keep_this_face(messi):
    """The reviewer's probe: the detector finds one other face inside Messi's box, not his own."""
    x, y, w, h = messi.box
    knee = (int(x + w / 2 - 15), int(y + 0.7 * h), 30, 30)
    assert knee[1] > messi.face[1] + messi.face[3]  # well below his face
    out = _still(messi, _Faces([(knee, False)]), detect_side=0)
    assert sharpness(out, messi.face) < 0.25 * sharpness(messi.frame, messi.face)
    out = _still(messi, _Faces([(knee, True)]), detect_side=0)  # an enrolled face, but not in this person's own head
    assert sharpness(out, messi.face) < 0.25 * sharpness(messi.frame, messi.face)


def test_f_an_enrolled_face_in_its_own_head_stays_sharp(messi):
    index = FaceIndex()
    index.enroll("stf_1", "Player", messi.frame, consent=True)
    out = _still(messi, index)
    assert sharpness(out, messi.face) > 0.6 * sharpness(messi.frame, messi.face)
    out = _still(messi, FaceIndex())  # nobody enrolled: the face goes
    assert sharpness(out, messi.face) < 0.25 * sharpness(messi.frame, messi.face)


# ---- G: after a person's false alarm only an alert verdict reopens ----

@pytest.mark.parametrize("status", ["unverified", "awaiting_human"])
def test_g_an_unsettled_verdict_after_a_false_alarm_stays_quiet(env, status):  # noqa: F811
    env.link(env.a, -1001)
    c = live(env, "fpA", 0)
    first = inc_.open_before_check(env.a, c)
    env.run(at(0))
    inc_.false_alarm(env.a, first.id, RAVI, at(5))
    env.run(at(5))
    posts = len(env.fake.of("sendMessage"))
    said = "Someone still in the water: the AI can't judge this, it would need an age."
    got = inc_.after_check(env.a, c, Verdict(status, said), "alt_1", incident_id=first.id, now=at(20))
    env.run(at(20))
    assert got.id == first.id and got.status == "false_alarm" and len(env.fake.of("sendMessage")) == posts
    assert inc_.list_incidents(env.a, ("open",)) == []
    late = inc_.after_check(env.a, c, Verdict(status, said), "alt_2", incident_id=first.id,
                            now=at(5 + Tuning().alerts.false_alarm_quiet_s + 1))
    assert late.id != first.id and late.status == "open"  # past the quiet window it is a moment again
    real = inc_.after_check(env.a, c, Verdict("alert", "Someone is face down in the water."), "alt_3",
                            incident_id=first.id, now=at(30))
    assert real.status == "open"


def test_g_a_trigger_closed_as_a_false_alarm_stays_quiet_when_the_ai_cannot_settle_it(env):  # noqa: F811
    """A warning watch's incident opened after its check: the very trigger a person closed stays quiet."""
    env.link(env.a, -1001)
    inc_.set_warnings(env.a, True)
    c = Candidate("wat_gate", env.cams["gate"], at(0).timestamp(), 2, "warning", {}, [], "fpG", "Gate open late")
    first = inc_.after_check(env.a, c, Verdict("alert", "The gate is open."), "alt_1", now=at(0))
    env.run(at(0))
    inc_.false_alarm(env.a, first.id, RAVI, at(10))
    env.run(at(10))
    posts = len(env.fake.of("sendMessage"))
    quiet = inc_.after_check(env.a, c, Verdict("awaiting_human", "A person should look."), "alt_2", now=at(40))
    env.run(at(40))
    assert quiet.id == first.id and len(env.fake.of("sendMessage")) == posts
    other = Candidate("wat_gate", env.cams["gate"], at(50).timestamp(), 3, "warning", {}, [], "fpH", "Gate open late")
    assert inc_.after_check(env.a, other, Verdict("unverified", "Not checked."), "alt_3", now=at(50)).id != first.id


# ---- H: token paths in uvicorn's access log keep the line ----

def test_h_access_lines_keep_their_five_arguments():
    from uvicorn.logging import AccessFormatter
    from watchful.ingest.push import TokenPaths
    token = "tok_" + "x" * 30
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("10.0.0.1:5000", "GET", f"/cam/{token}/info", "1.1", 200), None)
    assert TokenPaths().filter(record) and len(record.args) == 5
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
    assert token not in line and "/cam/-/info" in line
    ws = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, '%s - "WebSocket %s" [accepted]',
                           ("10.0.0.1:5000", f"/ws/cam/{token}"), None)
    TokenPaths().filter(ws)
    assert ws.getMessage() == '10.0.0.1:5000 - "WebSocket /ws/cam/-" [accepted]'
    other = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                              ("10.0.0.1:5000", "GET", "/api/cam/face-model", "1.1", 200), None)
    TokenPaths().filter(other)
    assert other.args[2] == "/api/cam/face-model"

