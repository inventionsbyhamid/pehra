import json

import cv2
import numpy as np
import pytest

from watchful.agent.llm import FakeLLM
from watchful.search import PersonQuery, find_person
from watchful.store.blobs import LocalBlobStore
from watchful.store.db import EventRow, SqliteRepository
from watchful.vision.faces import ConsentRequired, FaceIndex
from watchful.vision.models import sample_path


def jpg(img):
    return cv2.imencode(".jpg", img)[1].tobytes()


@pytest.fixture
def world(tmp_path):
    repo, blobs = SqliteRepository(tmp_path / "w.db"), LocalBlobStore(tmp_path)
    player = cv2.imread(str(sample_path("basketball2.png")))[60:300, 30:150]
    blobs.put("cam/kf/1.jpg", jpg(player), "image/jpeg")
    blobs.put("cam/kf/2.jpg", jpg(np.full((120, 60, 3), 200, np.uint8)), "image/jpeg")
    rows = []
    for tid, emb in ((1, [1.0, 0.0]), (2, [0.0, 1.0])):
        for kind, ts in (("track_start", 100.0 * tid), ("track_end", 100.0 * tid + 30)):
            rows.append(EventRow(None, "cam", ts, kind, tid, "person", None, {}, f"cam/kf/{tid}.jpg", None, emb, None))
    repo.add_events(rows)
    return repo, blobs


def test_enrolled_person_found_by_face(world):
    repo, blobs = world
    faces = FaceIndex()
    faces.enroll("p1", "Player", cv2.imread(str(sample_path("basketball1.png"))), consent=True)
    got = find_person(PersonQuery(person_id="p1"), repo, blobs, faces=faces)
    assert [(s.track_id, s.method) for s in got] == [(1, "face")]
    assert got[0].start_ts == 100.0 and got[0].end_ts == 130.0


def test_photo_needs_consent(world):
    repo, blobs = world
    with pytest.raises(ConsentRequired):
        find_person(PersonQuery(photo=jpg(np.zeros((10, 10, 3), np.uint8))), repo, blobs, faces=FaceIndex())


def test_stranger_photo_finds_nobody(world):
    repo, blobs = world
    photo = jpg(cv2.imread(str(sample_path("messi5.jpg"))))
    assert find_person(PersonQuery(photo=photo, consent=True), repo, blobs, faces=FaceIndex()) == []


class Stub:
    def text(self, q):
        return np.array([1.0, 0.0], np.float32)

    def image(self, crop):
        return np.array([0.0, 1.0], np.float32)


def test_description_uses_embeddings_then_ai_check(world):
    repo, blobs = world
    llm = FakeLLM([json.dumps({"matches": [1]})])
    got = find_person(PersonQuery(description="a man in a red jacket"), repo, blobs, embedder=Stub(), llm=llm)
    assert [(s.track_id, s.method) for s in got] == [(1, "description")]
    assert len(llm.calls[0][2]) >= 1
