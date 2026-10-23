import json
from datetime import datetime
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import pytest

from watchful.agent.llm import FakeLLM
from watchful.indexer import index_once
from watchful.search import SearchQuery, search
from watchful.store.blobs import LocalBlobStore
from watchful.store.db import Camera, EventRow, SqliteRepository
from watchful.vision.faces import ConsentRequired, FaceIndex
from watchful.vision.models import sample_path

TZ = "Asia/Kolkata"
RED, BOX, PERSON, PHOTO = [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [0.4, 0.4, 0.4]


def jpg(img):
    return cv2.imencode(".jpg", img)[1].tobytes()


def unit(v):
    v = np.asarray(v, np.float32)
    return (v / np.linalg.norm(v)).tolist()


class Stub:
    """Text and images in a tiny 3d space: red things, boxes, people."""
    texts = {"a red car": RED, "someone carrying a box": BOX, "a photo": PHOTO, "a photo of a person": PERSON,
             "a photo of a car": PHOTO}

    def text(self, q):
        return np.asarray(unit(self.texts.get(q, PHOTO)), np.float32)

    def image(self, crop):
        return np.asarray(unit([crop.mean() / 255.0, 0.1, 0.1]), np.float32)


def ev(kind, ts, emb, cam="cam1", tid=None, cls=None, key="k.jpg"):
    return EventRow(None, cam, ts, kind, tid, cls, None, {}, key, None, unit(emb) if emb else None, None)


@pytest.fixture
def world(tmp_path):
    repo, blobs = SqliteRepository(tmp_path / "w.db"), LocalBlobStore(tmp_path)
    blobs.put("k.jpg", jpg(np.full((40, 40, 3), 120, np.uint8)), "image/jpeg")
    repo.add_camera(Camera("cam1", "Gate", "stream", "rtsp://x"))
    repo.add_camera(Camera("cam2", "Kitchen", "stream", "rtsp://y"))
    return repo, blobs


def test_text_finds_scenes_and_objects_best_first(world):
    repo, blobs = world
    repo.add_events([ev("scene", 100, [0.9, 0.1, 0.1]), ev("scene", 103, [0.88, 0.1, 0.1]),
                     ev("scene", 200, [0.1, 0.9, 0.1]),
                     ev("track_end", 300, [0.95, 0.05, 0.3], tid=4, cls="car")])
    r = search(SearchQuery(text="a red car"), repo, blobs, Stub())
    assert {x.ts for x in r.results} >= {100.0, 300.0} and 103.0 not in {x.ts for x in r.results}
    assert r.results[-1].ts == 200.0 or 200.0 not in {x.ts for x in r.results}
    assert all(x.thumbnail_url for x in r.results)


def test_the_ai_reads_time_and_camera_from_the_words(world):
    repo, blobs = world
    morning = datetime(2026, 10, 23, 9, 0, tzinfo=ZoneInfo(TZ)).timestamp()
    repo.add_events([ev("scene", morning, BOX, cam="cam2"), ev("scene", morning + 7200, BOX, cam="cam2"),
                     ev("scene", morning, BOX, cam="cam1")])
    llm = FakeLLM([json.dumps({"looks_like": "someone carrying a box", "camera": "Kitchen",
                               "since": morning - 600, "until": morning + 600, "classes": []}),
                   json.dumps({"matches": [1]})])
    r = search(SearchQuery(text="someone with a box in the kitchen around 9 this morning"), repo, blobs, Stub(),
               llm=llm, now=morning + 10000, tz=TZ)
    assert [(x.camera_id, x.ts) for x in r.results] == [("cam2", morning)]
    assert r.query["camera_id"] == "cam2" and r.results[0].confirmed


def test_ai_confirmed_results_come_first(world):
    repo, blobs = world
    repo.add_events([ev("scene", 100, [0.9, 0.1, 0.1]), ev("scene", 200, [0.8, 0.2, 0.1])])
    llm = FakeLLM([json.dumps({"looks_like": "a red car"}), json.dumps({"matches": [2]})])
    r = search(SearchQuery(text="a red car"), repo, blobs, Stub(), llm=llm)
    assert [x.ts for x in r.results] == [200.0, 100.0] and [x.confirmed for x in r.results] == [True, False]


def test_missing_embeddings_are_filled_by_the_indexer(world):
    repo, blobs = world
    repo.add_events([ev("scene", 100, None), ev("track_end", 101, None, tid=1, cls="person"), ev("enter", 102, None)])
    assert index_once(repo, blobs, Stub()) == 2
    rows = repo.query_events(camera_id="cam1")
    assert [r.embedding is not None for r in rows] == [True, True, False]


def test_photo_with_a_face_needs_consent(world):
    repo, blobs = world
    photo = jpg(cv2.imread(str(sample_path("basketball1.png"))))
    with pytest.raises(ConsentRequired):
        search(SearchQuery(photo=photo), repo, blobs, Stub(), faces=FaceIndex())


def test_photo_finds_the_same_face(world):
    repo, blobs = world
    player = cv2.imread(str(sample_path("basketball2.png")))[60:300, 30:150]
    blobs.put("p.jpg", jpg(player), "image/jpeg")
    repo.add_events([ev("track_end", 500, None, tid=9, cls="person", key="p.jpg"),
                     ev("track_end", 600, None, tid=10, cls="person")])
    photo = jpg(cv2.imread(str(sample_path("basketball1.png"))))
    r = search(SearchQuery(photo=photo, consent=True), repo, blobs, Stub(), faces=FaceIndex())
    assert [(x.track_id, x.method) for x in r.results] == [(9, "face")]


def test_enrolled_person_search(world):
    repo, blobs = world
    player = cv2.imread(str(sample_path("basketball2.png")))[60:300, 30:150]
    blobs.put("p.jpg", jpg(player), "image/jpeg")
    repo.add_events([ev("track_end", 500, None, tid=9, cls="person", key="p.jpg"),
                     ev("track_end", 600, None, tid=10, cls="person")])
    faces = FaceIndex()
    faces.enroll("per_1", "Player", cv2.imread(str(sample_path("basketball1.png"))), consent=True)
    r = search(SearchQuery(person_id="per_1"), repo, blobs, Stub(), faces=faces)
    assert [(x.track_id, x.method) for x in r.results] == [(9, "face")]


def test_unindexed_moments_are_reported_not_waited_for(world):
    from watchful.tuning import SignalTuning
    repo, blobs = world
    repo.add_events([ev("scene", 100 + i * 20, None) for i in range(5)])
    r = search(SearchQuery(text="a red car"), repo, blobs, Stub(), cfg=SignalTuning(search_embed_on_demand=2))
    assert "still indexing 3" in (r.problem or "").lower()
