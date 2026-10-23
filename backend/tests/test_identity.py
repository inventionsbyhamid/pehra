import cv2
import numpy as np

from watchful.store.db import Camera
from watchful.tuning import Tuning
from watchful.vision.faces import FaceMatch
from watchful.vision.perceive import Observation, Perception
from watchful.vision.reason import Reasoner


def sig(bgr):
    img = np.zeros((40, 20, 3), np.uint8)
    img[:] = bgr
    h = cv2.calcHist([cv2.cvtColor(img, cv2.COLOR_BGR2HSV)], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(h, h, 1.0, 0.0, cv2.NORM_L1)
    return h


GREEN, RED = sig((40, 160, 40)), sig((40, 40, 190))


def step(r, ts, observations):
    return r.step(Perception(ts, (640, 480), True, observations))


def test_the_name_carries_to_a_later_track_in_the_same_clothes():
    r = Reasoner(Camera("cam", "Door", "stream", None), Tuning(), postures=False)
    box = (10.0, 10.0, 40.0, 90.0)
    step(r, 0, [Observation(0, 1, "person", box, 0.9, face=FaceMatch("p1", "Sana", 0.8), signature=GREEN, started=True)])
    step(r, 5, [Observation(5, 1, "person", box, 0.9, signature=GREEN, ended=True)])
    tick = step(r, 200, [Observation(200, 2, "person", box, 0.9, signature=GREEN, started=True),
                         Observation(200, 3, "person", box, 0.9, signature=RED, started=True)])
    states = {s.track_id: s for s in tick.tracks}
    assert states[2].known_person_id == "p1" and states[2].known_by == "clothes"
    assert states[3].known_person_id is None
    tick = step(r, 2000, [Observation(2000, 4, "person", box, 0.9, signature=GREEN, started=True)])
    assert tick.tracks[0].known_person_id is None  # too long after the face was seen
