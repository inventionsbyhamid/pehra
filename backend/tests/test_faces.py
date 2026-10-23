import cv2
import numpy as np
import pytest

from watchful.vision.faces import ConsentRequired, FaceIndex, NoFaceFound
from watchful.vision.models import sample_path


def img(name):
    return cv2.imread(str(sample_path(name)))


@pytest.fixture
def index():
    idx = FaceIndex()
    idx.enroll("p1", "Player", img("basketball1.png"), consent=True)
    return idx


def test_enroll_requires_consent():
    with pytest.raises(ConsentRequired):
        FaceIndex().enroll("p1", "Player", img("basketball1.png"), consent=False)


def test_enroll_no_face_raises():
    wall = np.full((300, 300, 3), 180, np.uint8)
    with pytest.raises(NoFaceFound):
        FaceIndex().enroll("p2", "Wall", wall, consent=True)


def test_identify_same_person(index):
    # the same player one frame later, found inside a person box
    match = index.identify(img("basketball2.png"), (40, 80, 100, 250))
    assert match is not None and match.person_id == "p1" and match.score >= 0.363


def test_identify_stranger_returns_none(index):
    assert index.identify(img("messi5.jpg"), (190, 80, 100, 250)) is None
    assert len(index.people()) == 1


def test_remove_forgets(index):
    index.remove("p1")
    assert index.identify(img("basketball2.png"), (40, 80, 100, 250)) is None
