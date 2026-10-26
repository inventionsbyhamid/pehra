"""Final fix pass A (security, tenancy, privacy, abuse): the age guard on every way of writing an age (I1, M1), look
back search and Ask guarded too (I2), AI output about age failing closed (I3), no search by photo (I4), faces only on
entry cameras (I5), request body caps (I6), picture bombs (I7), the demo's camera slots (I8), the AI caps (I9), and the
minors (M2 to M8, appearance lookouts off)."""
import json
import logging
import struct
import time
import zlib
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from accounts import BASE, browser
from test_age_guard import AGNOSTIC, looks, reply, words_only
from test_agent import cand, ctx as tool_ctx, step
from test_api import StubManager
from watchful.agent.compiler import (CompileContext, Invalid, age_in_name, check_watch, compile_watch, mentions_age,
                                     place_phrases)
from watchful.agent.llm import AILimitReached, FakeLLM, LimitedLLM
from watchful.agent.verifier import verify
from watchful.api import create_app
from watchful.config import Settings
from watchful.store.db import Camera, EventRow, SqliteRepository, WatchRow
from watchful.tuning import load_tuning
from watchful.vision.zones import Zone

ROOT = Path(__file__).resolve().parents[2]
AGE_WORDS = "I can't tell anyone's age, and I never watch children as a group."


def app_for(tmp_path, llm=None, tuning=None, **settings):
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None, **settings)
    return create_app(s, llm=llm or FakeLLM([]), manager=StubManager(), tuning=tuning)


# ---- I1: every way of writing an age the review found ----

PROBES = {
    "numbers": ["someone under 18", "a person below eighteen", "not yet 18", "looks under 13", "a person about 7",
                "a 5 y/o", "in their single digits"],
    "heights": ["shorter than the pool railing", "a person 110cm", "about 1 metre", "less than a metre"],
    "scripts": ["मुलगा", "मुलगी", "சிறுவன்", "పిల్లలు", "ಮಗು", "બાળક", "ਬੱਚਾ", "بچہ"],
    "latin": ["beti", "kishore", "ponnu", "ammayi", "meye", "magu", "kunju", "lad", "lass", "son", "daughter", "beta",
              "kishor"],
    "devanagari numbers": ["पाँच साल का", "५ साल की", "बचचा"],
    "obfuscation": ["ｃｈｉｌｄ", "a CH-ILD", "a k-i-d", "c h i l d", "a k i d"],
    "endings": ["குழந்தைகள்", "ಮಕ್ಕಳನ್ನು", "పిల్లలను", "বাচ্চাদের", "a larki", "Someone around eight"],
    "size": ["someone small", "the person is short", "a tiny swimmer", "a little person"],
}
ALL_PROBES = [t for texts in PROBES.values() for t in texts]


@pytest.mark.parametrize("words", ALL_PROBES)
def test_i1_every_probe_is_an_age(words):
    assert mentions_age(words) and mentions_age(f"tell me when {words} is at the pool"), words


@pytest.mark.parametrize("key", ["cond", "not_text", "zone", "subject"])
@pytest.mark.parametrize("words", ALL_PROBES)
def test_i1_no_probe_reaches_the_image_model(words, key):
    with pytest.raises(Invalid):
        check_watch(looks(words, key), "someone at the pool", words_only())


@pytest.mark.parametrize("words", ALL_PROBES)
def test_i1_a_request_with_a_probe_is_a_cannot(words):
    watch = {"name": "Someone near the water", "subject": {"classes": ["person"]},
             "conditions": [{"signal": "in_zone", "zones": ["water"]}], "verify": "Is someone near the water?"}
    r = compile_watch(f"tell me when {words} goes near the water", words_only(), FakeLLM([reply(watch)]))
    assert r.cannot and r.cannot[-1].reason == AGE_WORDS, words


def test_i1_a_card_with_a_cannot_about_age_is_never_saved(tmp_path):
    """The review's live probe: "someone under 18" showed a Cannot on the card and Save still saved it. However the
    client sends it (the words, the card's Cannot, or neither), it is refused and nothing is saved."""
    watch = {"name": "Someone at the pool", "subject": {"classes": ["person"]},
             "conditions": [{"signal": "in_zone", "zones": ["water"]}], "verify": "Is someone in the water?"}
    app = app_for(tmp_path, llm=FakeLLM([reply(watch)] * 3))
    c = browser(app)
    cam = c.post("/api/cameras", json={"name": "Pool", "kind": "stream", "url": "rtsp://pool",
                                       "purpose": "pool"}).json()
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    c.patch(f"/api/cameras/{cam['id']}", json={"zones": [{"id": "w", "name": "water", "kind": "water",
                                                          "polygon": square}]})
    card = c.post(f"/api/cameras/{cam['id']}/watches", json={"text": "someone under 18 in the water"}).json()
    assert card["saved"] is False and card["cannot"][-1]["reason"] == AGE_WORDS
    for body in ({"text": "someone under 18 in the water", "watch": card["watch"], "cannot": card["cannot"]},
                 {"watch": card["watch"], "cannot": card["cannot"]},
                 {"text": "someone under 18 in the water", "watch": card["watch"]},
                 {"text": "a k-i-d in the water", "watch": card["watch"]}):
        res = c.post(f"/api/cameras/{cam['id']}/watches", json=body)
        assert res.status_code == 400 and "age" in res.json()["detail"], (body, res.text)
    assert c.get(f"/api/cameras/{cam['id']}/watches").json() == []
    ok = c.post(f"/api/cameras/{cam['id']}/watches", json={"text": "someone in the water", "watch": card["watch"]})
    assert ok.status_code == 200 and ok.json()["saved"] is True


def test_i1_the_four_age_agnostic_watches_still_save():
    for text, (cam, spec) in AGNOSTIC.items():
        zone = {"pool": {"water": "water"}, "gate": {"gate area": "area"}}[cam]
        r = check_watch(spec, text, CompileContext(zones=zone, meaning=lambda t: False))
        assert r.watch is not None and r.cannot == [], text


# ---- M1: ordinary words are not ages ----

ORDINARY = ["teen log gate par", "a baby blue scooter", "the minor gate", "a 2 year old car", "a youth hostel guest",
            "the building boy", "an old man with a stick", "an elderly person", "a senior citizen",
            "someone within 2 metres of the pool edge", "a person 2 metres from the gate", "water about 1 metre deep",
            "someone stays under 5 minutes", "looks like 5 people at the gate", "a 7 year warranty",
            "a car parked for under 5 minutes", "a group of 5 people", "one of 3 guards", "the U12 block",
            "a small group of people", "someone with a small bag", "someone stays a short while", "a little dog"]


@pytest.mark.parametrize("words", ORDINARY)
def test_m1_ordinary_words_are_not_ages(words):
    assert not mentions_age(words), words


@pytest.mark.parametrize("name", ["U1 parking", "Tower U2", "बच्चों का पार्क", "kids play area 2", "Kids pool B",
                                  "kids' play area", "senior citizens park"])
def test_m1_zone_names_that_are_places(name):
    assert not age_in_name(name), name
    assert not mentions_age(f"someone in the {name} after 10 PM", [name])


def test_m1_a_place_name_with_an_age_word_is_still_masked_and_a_child_is_not():
    names = ["बच्चों का पार्क", "kids play area 2"]
    assert len(place_phrases(names)) == 2
    assert mentions_age("a child in the kids play area 2", names)


def test_m1_staff_names_are_names():
    """An enrolled staff member called Kishore or Munna is a name, not an age."""
    ctx = CompileContext(zones={"gate area": "area"}, people={"per_1": "Kishore", "per_2": "Munna"},
                         meaning=lambda t: False, purpose="main gate")
    assert not mentions_age("tell me when Kishore leaves the gate area", ctx.place_names)
    assert mentions_age("tell me when a kishore leaves", CompileContext().place_names)


# ---- I2: look back search and Ask ----

def search_world(tmp_path):
    from watchful.store.blobs import LocalBlobStore
    repo, blobs = SqliteRepository(tmp_path / "w.db", "ws_s"), LocalBlobStore(tmp_path)
    repo.add_camera(Camera("cam1", "Pool", "stream", "rtsp://x", [Zone("z", "kids pool", [(0, 0), (1, 0), (1, 1)])]))
    return repo, blobs


class Texts:
    def text(self, q):
        return np.ones(3, np.float32) / np.sqrt(3)

    def image(self, crop):
        return np.ones(3, np.float32) / np.sqrt(3)


def test_i2_search_refuses_a_question_or_caption_about_age(tmp_path):
    from watchful.search import AgeRefused, SearchQuery, search
    repo, blobs = search_world(tmp_path)
    never = lambda t: False  # noqa: E731
    for words in ("someone under 18 near the pool", "a little girl in a pink frock", "बच्चा", "a k-i-d"):
        llm = FakeLLM([])
        with pytest.raises(AgeRefused) as e:
            search(SearchQuery(text=words), repo, blobs, Texts(), llm=llm, meaning=never)
        assert str(e.value) == AGE_WORDS and llm.calls == []  # refused before the AI sees it
    for caption in ({"looks_like": "a little girl in a pink frock"}, {"looks_like": "a person", "not_like": "a kid"},
                    {"looks_like": "a tiny swimmer"}):
        llm = FakeLLM([json.dumps(caption)])
        with pytest.raises(AgeRefused):
            search(SearchQuery(text="the one in pink near the pool"), repo, blobs, Texts(), llm=llm,
                   meaning=lambda t: t == "a tiny swimmer")
    ok = search(SearchQuery(text="someone in the kids pool"), repo, blobs, Texts(), llm=FakeLLM([]), meaning=never)
    assert ok.results == []


def test_i2_ask_refuses_a_question_about_age_and_never_shows_an_answer_about_one(tmp_path):
    from watchful.agent.ask import AskContext, ask
    from watchful.agent.prompting import render
    from watchful.store.blobs import LocalBlobStore
    repo, blobs = SqliteRepository(tmp_path / "w.db"), LocalBlobStore(tmp_path)
    ctx = AskContext(repo, blobs, None, "Asia/Kolkata")
    llm = FakeLLM([])
    a = ask("when did the kids come to the pool", None, ctx, llm, now=time.time())
    assert a.text == AGE_WORDS and a.items == [] and llm.calls == []
    said = json.dumps({"tool": "answer", "args": {"text": "A small child, maybe 3, came at 09:02.", "items": []},
                       "why": "the little girl came in"})
    a = ask("who came to the pool this morning", None, ctx, FakeLLM([said]), now=time.time(), meaning=lambda t: False)
    assert a.text == AGE_WORDS and a.items == []
    assert not any(mentions_age(json.dumps([t.why, t.args])) for t in a.trace)
    for prompt in ("ask", "search"):
        assert "Age rule" in render(prompt, tools="", now="now", tz="Asia/Kolkata", cameras=[], classes=[])


def test_i2_the_search_api_refuses_in_plain_words(tmp_path):
    app = app_for(tmp_path)
    c = browser(app)
    res = c.post("/api/search", json={"text": "someone under 18"})
    assert res.status_code == 400 and res.json()["detail"] == AGE_WORDS


# ---- I3: what the AI says about age never reaches a person ----

@pytest.mark.parametrize("said", ["A small child, maybe 3", "Someone around eight", "a larki near the water",
                                  "Someone, maybe 3, is in the pool."])
def test_i3_an_ai_summary_about_age_is_replaced_whole(tmp_path, said):
    v = verify(cand("critical"), tool_ctx(tmp_path), FakeLLM([step("raise_alert", summary=said, severity="critical",
                                                                   reason="alone")]), meaning=lambda t: False)
    assert (v.status, v.summary, v.reason) == ("alert", "Car enters: the AI check agreed it is real.", "")
    assert v.severity == "critical"
    assert not any(mentions_age(json.dumps([t.why, t.args])) for t in v.trace)
    assert v.trace[-1].args["summary"] == "Left out: it described someone in a way the system never keeps."


def test_i3_the_meaning_check_catches_what_no_word_says(tmp_path):
    """"Pigtails" is no age word; the meaning check (here a stand in) still replaces the summary, after the society's
    place names are taken out (so "the kids pool" is not what it reads)."""
    seen = []

    def meaning(text):
        seen.append(text)
        return "pigtails" in text
    c = tool_ctx(tmp_path)
    c.repo.add_camera(Camera(None, "Pool", "stream", None, [Zone("z", "kids pool", [(0, 0), (1, 0), (1, 1)])]))
    said = FakeLLM([step("raise_alert", summary="A swimmer with pigtails is in the kids pool.")])
    v = verify(cand(), c, said, meaning=meaning)
    assert v.summary == "Car enters: the AI check agreed it is real."
    assert "A swimmer with pigtails is in the the place." in seen
    v = verify(cand(), tool_ctx(tmp_path / "b"), FakeLLM([step("dismiss", reason="Just a swimmer with pigtails.")]),
               meaning=meaning)
    assert (v.status, v.summary) == ("dismissed", "Car enters: the AI check found nothing to worry about.")


def test_i3_a_meaning_check_that_cannot_run_fails_closed(tmp_path):
    def broken(text):
        raise OSError("no text tower")
    v = verify(cand(), tool_ctx(tmp_path), FakeLLM([step("raise_alert", summary="A car drove in.")]), meaning=broken)
    assert v.summary == "Car enters: the AI check agreed it is real."


def test_i3_the_real_meaning_check_on_a_summary(tmp_path):
    v = verify(cand(), tool_ctx(tmp_path), FakeLLM([step("raise_alert", summary="A tiny swimmer is in the pool.")]))
    assert v.summary == "Car enters: the AI check agreed it is real."
    v = verify(cand(), tool_ctx(tmp_path / "b"), FakeLLM([step("raise_alert", summary="A car drove in at the gate.")]))
    assert v.summary == "A car drove in at the gate."


def test_i3_alerts_and_their_socket_message_leave_the_trace_out(tmp_path):
    from watchful.store.db import AlertRow
    app = app_for(tmp_path)
    c = browser(app)
    repo = app.state.ctx.repo_for("ws_test_society")
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://g"))
    repo.add_alert(AlertRow(None, cam.id, None, time.time(), "warning", "alert", "A car drove in.", None, None,
                            [{"step": 1, "tool": "raise_alert", "args": {"summary": "x"}, "why": "y", "result": "",
                              "ms": 1}], None, "fp", None))
    shown = c.get("/api/alerts").json()
    assert shown and all("trace" not in a for a in shown)
    import inspect
    from watchful import workers
    assert '!= "trace"' in inspect.getsource(workers.WorkerManager._store)


# ---- I4: no search by photo ----

def test_i4_the_search_api_takes_no_photo_and_person_search_needs_a_supervisor(tmp_path):
    app = app_for(tmp_path)
    picture = cv2.imencode(".jpg", np.zeros((32, 32, 3), np.uint8))[1].tobytes()
    owner, viewer = browser(app), browser(app, "v@example.com", role="viewer")
    for c in (owner, viewer):
        res = c.post("/api/search", files={"photo": ("a.jpg", picture, "image/jpeg")}, data={"consent": "true"})
        assert res.status_code == 415 and "photo" in res.json()["detail"]
    res = viewer.post("/api/search", json={"person_id": "per_x"})
    assert res.status_code == 403
    res = owner.post("/api/search", json={"person_id": "per_x"})
    assert res.status_code == 400 and res.json()["detail"] == "that person is not enrolled"


def test_i4_person_search_looks_only_at_entry_cameras(tmp_path):
    from watchful.search import SearchQuery, search
    from watchful.store.blobs import LocalBlobStore

    class Faces:
        def __init__(self):
            self.looked = []

        def people(self):
            return [type("E", (), {"person_id": "per_1"})()]

        def share(self, people):
            return self

        def identify(self, img, box):
            self.looked.append(img.shape)
            return type("M", (), {"score": 0.9})()

    repo, blobs = SqliteRepository(tmp_path / "w.db", "ws_s"), LocalBlobStore(tmp_path)
    repo.add_camera(Camera("gate", "Gate", "stream", "rtsp://g", purpose="main gate"))
    repo.add_camera(Camera("pool", "Pool", "stream", "rtsp://p", purpose="pool"))
    jpg = cv2.imencode(".jpg", np.zeros((20, 10, 3), np.uint8))[1].tobytes()
    blobs.put("ws_s/k.jpg", jpg, "image/jpeg")
    repo.add_events([EventRow(None, cam, 100.0 + i, "track_end", i, "person", None, {}, "ws_s/k.jpg", None, None, None)
                     for i, cam in enumerate(("gate", "pool", "pool"))])
    faces = Faces()
    r = search(SearchQuery(person_id="per_1"), repo, blobs, Texts(), faces=faces)
    assert [h.camera_id for h in r.results] == ["gate"] and len(faces.looked) == 1


# ---- I5: faces only on entry cameras ----

def staff_ctx(purpose):
    return CompileContext(zones={"gate area": "area"}, people={"per_1": "Ramesh"}, purpose=purpose,
                          meaning=lambda t: False)


STAFF = {"name": "Unknown person at the gate", "subject": {"classes": ["person"], "who": "unknown"},
         "conditions": [{"signal": "in_zone", "zones": ["gate area"]}], "verify": "Is someone there?"}


@pytest.mark.parametrize("who", ["unknown", "known", "per_1"])
def test_i5_a_watch_about_who_someone_is_runs_on_entry_cameras_only(who):
    spec = {**STAFF, "subject": {"classes": ["person"], "who": who}}
    for purpose in ("main gate", "service gate", "lobby"):
        assert check_watch(spec, "someone at the gate", staff_ctx(purpose)).watch.subject.who == who
    for purpose in ("pool", "parking", "perimeter", "other", None):
        with pytest.raises(Invalid) as e:
            check_watch(spec, "someone at the gate", staff_ctx(purpose))
        assert "entry cameras" in str(e.value)
    said = json.dumps({"watch": spec, "coverage": [{"clause": "people not on the staff list", "signal": "faces",
                                                    "tier": "B"}], "setup": [], "cannot": [], "about_age": False})
    r = compile_watch("someone not on the staff list in the gate area", staff_ctx("pool"), FakeLLM([said]))
    assert r.watch.subject.who == "anyone" and r.cannot and "entry cameras" in r.cannot[-1].reason
    assert r.cannot[-1].alternative.startswith("anyone") and not any(c.signal == "faces" for c in r.coverage)
    r = compile_watch("someone not on the staff list in the gate area", staff_ctx("main gate"), FakeLLM([reply(spec)]))
    assert r.watch.subject.who == who and r.cannot == []


def test_i5_presets_and_lookouts_for_a_person_need_an_entry_camera(tmp_path):
    from watchful.vision.faces import Enrollment
    app = app_for(tmp_path)
    c = browser(app)
    pool = c.post("/api/cameras", json={"name": "Pool", "kind": "stream", "url": "rtsp://p", "purpose": "pool"}).json()
    gate = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://g",
                                        "purpose": "main gate"}).json()
    app.state.ctx.repo_for("ws_test_society").add_enrollment(Enrollment("per_1", "Ramesh", [0.5] * 128))
    intruder = {"preset": "possible_intruder", "fill": {"entries": [], "climbs": []}}
    assert c.post(f"/api/cameras/{pool['id']}/watches", json=intruder).status_code == 400
    assert c.post(f"/api/cameras/{gate['id']}/watches", json=intruder).status_code == 200
    res = c.post("/api/lookouts", json={"camera_ids": [gate["id"], pool["id"]], "person_id": "per_1"})
    assert res.status_code == 400 and "entry cameras" in res.json()["detail"]
    assert c.post("/api/lookouts", json={"camera_ids": [gate["id"]], "person_id": "per_1"}).status_code == 200


def test_i5_a_site_watch_about_staff_never_runs_on_a_pool_camera(tmp_path):
    from watchful.rules.watch import select_watches
    repo = SqliteRepository(tmp_path / "w.db")
    pool = repo.add_camera(Camera(None, "Pool", "stream", "rtsp://p", [Zone("z", "gate area", [(0, 0), (1, 0),
                                                                                               (1, 1)])],
                                  purpose="pool"))
    repo.add_watch(WatchRow(None, None, "staff", {**STAFF, "scope": "site"}, True))
    ok, notes = select_watches(repo.list_watches(pool.id), pool.id, pool.zones, entry=False)
    assert ok == [] and "entry cameras" in notes[0]
    ok, _ = select_watches(repo.list_watches(pool.id), pool.id, pool.zones, entry=True)
    assert len(ok) == 1


def test_i5_a_device_camera_keeps_faces_only_at_an_entry(tmp_path):
    from watchful.ingest.push import needs_faces
    repo = SqliteRepository(tmp_path / "w.db")
    staff = {"name": "Staff arrive", "subject": {"classes": ["person"], "who": "known"},
             "conditions": [{"signal": "present"}]}
    for purpose, keeps in (("pool", False), ("perimeter", False), ("service gate", True), ("lobby", True)):
        cam = repo.add_camera(Camera(None, purpose, "webcam", None, purpose=purpose))
        repo.add_watch(WatchRow(None, cam.id, "staff", staff, True))
        assert needs_faces(repo, cam.id) is keeps, purpose
        cam.readers = ["faces"]
        repo.update_camera(cam)
        assert needs_faces(repo, cam.id) is keeps, purpose


def test_i5_a_purpose_change_makes_a_sending_device_check_its_blur(tmp_path):
    from watchful.ingest.push import push_parts
    app = app_for(tmp_path)
    c = browser(app)
    cam = c.post("/api/cameras", json={"name": "Desk", "kind": "webcam", "purpose": "lobby"}).json()

    class Device:
        nudged = 0

        def nudge(self):
            self.nudged += 1
    device = Device()
    push_parts(app.state.ctx)[0]._senders[cam["id"]] = [device]
    assert c.patch(f"/api/cameras/{cam['id']}", json={"name": "Front desk"}).status_code == 200
    assert device.nudged == 0
    assert c.patch(f"/api/cameras/{cam['id']}", json={"purpose": "pool"}).status_code == 200
    assert device.nudged == 1


# ---- I6: request bodies have a size limit ----

def test_i6_a_large_body_is_refused_before_it_is_read(tmp_path):
    app = app_for(tmp_path)
    anon = TestClient(app, base_url=BASE, headers={"origin": BASE})
    big = b"{" + b" " * (2 << 20) + b"}"
    res = anon.post("/api/cameras", content=big, headers={"content-type": "application/json"})
    assert res.status_code == 413
    res = anon.post("/api/billing/webhook", content=big)
    assert res.status_code == 413

    def chunks():  # no Content-Length: counted as it arrives
        for _ in range(3):
            yield b" " * (1 << 20)
    res = anon.post("/api/cameras", content=chunks(), headers={"content-type": "application/json"})
    assert res.status_code == 413
    video = {"file": ("v.mp4", b"\0" * (3 << 20), "video/mp4")}
    assert anon.post("/api/uploads", files=video).status_code == 413  # signed out: the default limit
    assert anon.post("/api/staff", files={"photos": ("a.jpg", b"\0" * (2 << 20), "image/jpeg")}).status_code == 413
    member = browser(app)
    assert member.post("/api/uploads", files=video).status_code == 400  # signed in: read, then found not a video
    assert member.post("/api/cameras", content=big, headers={"content-type": "application/json"}).status_code == 413
    res = member.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://gate/live"})
    assert res.status_code == 200, res.text


def test_i6_caddy_caps_bodies_too():
    caddy = (ROOT / "deploy" / "Caddyfile").read_text()
    assert "request_body {\n\t\tmax_size 210MB\n\t}" in caddy


# ---- I7: picture bombs ----

def png_header(width, height) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(b"\0" * 64)) + chunk(b"IEND", b"")


def jpeg_claiming(width, height) -> bytes:
    real = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))[1].tobytes()
    i = real.index(b"\xff\xc0")
    return real[:i + 5] + struct.pack(">HH", height, width) + real[i + 9:]


def test_i7_sizes_come_from_the_header_and_a_bomb_is_refused_undecoded(monkeypatch):
    from watchful.vision import decode
    picture = np.zeros((30, 40, 3), np.uint8)
    for ext in (".jpg", ".png", ".webp", ".bmp"):
        assert decode.image_size(cv2.imencode(ext, picture)[1].tobytes()) == (40, 30), ext
    assert decode.image_size(b"GIF89a" + struct.pack("<HH", 40, 30)) == (40, 30)
    assert decode.image_size(b"II*\0 a tiff") is None
    decoded = []
    monkeypatch.setattr(decode.cv2, "imdecode", lambda *a: decoded.append(1))
    for bomb in (png_header(30000, 30000), jpeg_claiming(20000, 20000)):
        with pytest.raises(decode.TooManyPixels):
            decode.decode_image(bomb, 40_000_000)
    assert decoded == []


def test_i7_uploads_and_device_frames_are_checked(tmp_path):
    from watchful.ingest.sources import PushSource
    tuning = load_tuning(None, {"limits": {"image_max_pixels": 1000}})
    app = app_for(tmp_path, tuning=tuning)
    c = browser(app)
    cam = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://g"}).json()
    c.patch(f"/api/cameras/{cam['id']}", json={"zones": [{"id": "z", "name": "gate", "polygon": [[0, 0], [1, 0],
                                                                                               [1, 1]]}]})
    big = cv2.imencode(".png", np.zeros((40, 40, 3), np.uint8))[1].tobytes()
    res = c.post(f"/api/cameras/{cam['id']}/zones/z/examples", data={"state": "open"},
                 files={"file": ("a.png", big, "image/png")})
    assert res.status_code == 400 and "too many pixels" in res.json()["detail"]
    res = c.post("/api/staff", data={"name": "Asha", "role": "maid", "consent": "true"},
                 files=[("photos", (f"{i}.png", big, "image/png")) for i in range(3)])
    assert res.status_code == 400 and "too many pixels" in res.json()["detail"]
    src = PushSource(fps=100, width=64, max_pixels=1000)
    src.push(jpeg_claiming(20000, 20000), 1.0)
    assert src.read(timeout=0.2) is None
    src.push(cv2.imencode(".jpg", np.zeros((20, 20, 3), np.uint8))[1].tobytes(), 2.0)
    assert src.read(timeout=0.5) is not None


def test_i7_the_service_units_cap_opencv_too():
    for unit in ("watchful-app.service", "watchful-worker.service"):
        text = (ROOT / "deploy" / "systemd" / unit).read_text()
        assert "Environment=OPENCV_IO_MAX_IMAGE_PIXELS=40000000" in text, unit


# ---- I8: the demo's camera slots ----

def test_i8_a_visitors_camera_slots_are_shared_fairly(tmp_path):
    from test_demo import _sandbox, make, me
    from watchful.api.demo import MADE_WAY, live_refusal
    from watchful.ingest.push import CLOSE_BUSY, Sender, push_parts
    app, _ = make(tmp_path, demo_live_cameras=3)
    ctx = app.state.ctx
    hub = push_parts(ctx)[0]

    def own(c):
        made = c.post("/api/cameras", json={"name": "Mine", "kind": "webcam"}).json()
        return ctx.repo_for(me(c)["workspace"]["id"]).get_camera(made["id"])

    class Loop:
        def call_soon_threadsafe(self, fn):
            fn()
    a, _ = _sandbox(app, ip="198.51.100.1")
    laptop, phone = own(a), own(a)
    hub._senders[laptop.id] = [Sender(Loop())]
    assert "one of your cameras at a time" in live_refusal(ctx, phone)  # one per sandbox
    b, _ = _sandbox(app, ip="198.51.100.2")  # the same /24 network
    c, _ = _sandbox(app, ip="198.51.100.3")
    b_cam, c_cam = own(b), own(c)
    assert live_refusal(ctx, b_cam) is None
    hub._senders[b_cam.id] = [Sender(Loop())]
    assert "Your network already has 2 cameras" in live_refusal(ctx, c_cam)  # two per network
    d, _ = _sandbox(app, ip="192.0.2.10")
    e, _ = _sandbox(app, ip="203.0.113.20")
    d_cam, e_cam = own(d), own(e)
    hub._senders[d_cam.id] = [Sender(Loop())]
    assert "The demo is busy" in live_refusal(ctx, e_cam)  # 3 live, nobody past their turn
    oldest = hub._senders[laptop.id][0]
    oldest.since -= 11 * 60
    assert live_refusal(ctx, e_cam) is None  # the one live longest makes way
    assert (oldest.code, oldest.reason) == (CLOSE_BUSY, MADE_WAY) and len(MADE_WAY.encode()) <= 123


def test_i8_a_sender_with_no_pictures_gives_its_place_back(tmp_path):
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect
    from test_push import Server
    from watchful.api.live import IDLE
    srv = Server(tmp_path)
    try:
        srv.app.state.ctx.tuning.limits.demo_idle_sender_s = 1.0
        visitor = TestClient(srv.app, base_url=f"http://{srv.host}", headers={"origin": f"http://{srv.host}"})
        assert visitor.post("/api/demo/login").status_code == 201
        cam = visitor.post("/api/cameras", json={"name": "Mine", "kind": "webcam"}).json()
        issued = visitor.post(f"/api/cameras/{cam['id']}/device-token").json()
        with connect(f"ws://{srv.host}/ws/cam/{issued['token']}") as ws:
            json.loads(ws.recv(timeout=5))
            started = time.time()
            with pytest.raises(ConnectionClosed) as closed:
                ws.recv(timeout=5)
            assert time.time() - started < 4
            assert (closed.value.rcvd.code, closed.value.rcvd.reason) == (4429, IDLE)
    finally:
        srv.srv.should_exit = True


# ---- I9: the AI caps ----

def test_i9_a_person_makes_at_most_three_societies(tmp_path):
    app = app_for(tmp_path)
    c = browser(app)  # owns one already
    made = [c.post("/api/workspaces", json={"name": f"Society {i}", "timezone": "Asia/Kolkata"}).status_code
            for i in range(3)]
    assert made == [201, 201, 403]


def test_i9_the_whole_deployment_has_an_ai_cap(tmp_path):
    from watchful.agent.verifier import verify as check
    from accounts import society_repo
    s = Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None)
    repos = [society_repo(s, n) for n in ("A", "B")]
    for _ in range(3):
        repos[0].record_ai_call("verify")
    llm = LimitedLLM(FakeLLM(["{}"] * 5), repos[1], limit=100, total=3)
    with pytest.raises(AILimitReached):
        llm.complete("s", [])
    v = check(cand("critical"), tool_ctx(tmp_path / "t"), llm, meaning=lambda t: False)
    assert v.status == "unverified" and "limit" in v.summary  # never silence: a person still sees it
    assert LimitedLLM(FakeLLM(["ok"]), repos[1], limit=100, total=4).complete("s", []) == "ok"
    from watchful.components import make_llm
    assert make_llm(s.model_copy(update={"ai_total_daily": 7}), repos[1]).total == 7


# ---- minors ----

def test_m2_every_token_path_and_signed_link_is_cut_from_the_app_log():
    import re
    from uvicorn.logging import AccessFormatter
    from watchful.ingest.push import TOKEN_PREFIXES, TokenPaths
    caddy = (ROOT / "deploy" / "Caddyfile").read_text()
    listed = re.search(r"request>uri regexp \^\(/\(\?:([^)]*)\)/\)", caddy).group(1).split("|")
    assert sorted(listed) == sorted(TOKEN_PREFIXES)
    token = "Zk3" * 15
    for path in (f"/auth/link/{token}", f"/join/{token}", f"/api/invites/{token}", f"/telegram/webhook/{token}",
                 f"/cam/{token}/info", f"/api/blobs/ws/a.jpg?exp=1&s={token}"):
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                   ("10.0.0.1:5000", "GET", path, "1.1", 200), None)
        assert TokenPaths().filter(record) and len(record.args) == 5
        line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
        assert token not in line, line


def test_m3_a_sandbox_can_not_turn_readers_on(tmp_path):
    from test_demo import _sandbox, make
    app, _ = make(tmp_path)
    a, _ = _sandbox(app)
    own = a.post("/api/cameras", json={"name": "Mine", "kind": "webcam"}).json()
    res = a.patch(f"/api/cameras/{own['id']}", json={"readers": ["faces"]})
    assert res.status_code == 403 and "demo" in res.json()["detail"]
    assert a.patch(f"/api/cameras/{own['id']}", json={"name": "Desk", "readers": []}).status_code == 200


def test_m4_openrouter_asks_providers_that_keep_nothing():
    import httpx
    from watchful.agent.llm import Msg, OpenRouterLLM
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    llm = OpenRouterLLM(Settings(session_secret="s", _env_file=None, openrouter_api_key="k"),
                        client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert llm.complete("s", [Msg("user", "hi")]) == "ok"
    assert sent[0]["provider"]["data_collection"] == "deny"


def test_m6_a_clip_still_is_decoded_once(tmp_path, monkeypatch):
    import watchful.api.cameras as cameras
    clips = tmp_path / "uploads"
    clips.mkdir()
    clip = clips / "a.avi"
    out = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    for _ in range(20):
        out.write(np.full((48, 64, 3), 90, np.uint8))
    out.release()
    opened = []
    real = cv2.VideoCapture
    monkeypatch.setattr(cameras.cv2, "VideoCapture", lambda url: opened.append(url) or real(url))
    app = app_for(tmp_path)
    c = browser(app)
    cam = app.state.ctx.repo_for("ws_test_society").add_camera(Camera(None, "Clip", "file", str(clip)))
    for _ in range(3):
        res = c.get(f"/api/cameras/{cam.id}/frame")
        assert res.status_code == 200 and res.content[:2] == b"\xff\xd8"
    assert opened == [str(clip)]


def test_m7_the_api_pages_are_off_unless_asked_for(tmp_path):
    app = app_for(tmp_path)
    anon = TestClient(app, base_url=BASE)
    for path in ("/docs", "/api/docs"):
        assert "swagger" not in anon.get(path).text.lower()
    for path in ("/openapi.json", "/api/openapi.json"):
        assert '"openapi"' not in anon.get(path).text
    dev = TestClient(app_for(tmp_path / "dev", api_docs=True), base_url=BASE)
    assert dev.get("/api/openapi.json").json()["info"]["title"]
    assert "swagger" in dev.get("/api/docs").text.lower()
    assert "swagger" not in dev.get("/docs").text.lower()  # the site's documentation, not the API's


def test_appearance_lookouts_are_off_unless_set(tmp_path):
    app = app_for(tmp_path)
    c = browser(app)
    repo = app.state.ctx.repo_for("ws_test_society")
    gate = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://g", purpose="main gate"))
    seen = EventRow(None, gate.id, time.time(), "enter", 3, "person", None, {}, None, [0.2], [0.3], None)
    repo.add_events([seen])
    for body in ({"description": "a person in a red shirt"}, {"event_id": seen.id}):
        res = c.post("/api/lookouts", json={"camera_ids": [gate.id], **body})
        assert res.status_code == 403 and "how they look" in res.json()["detail"], body
    assert repo.list_watches(gate.id) == []
    on = app_for(tmp_path / "on", appearance_lookouts=True)
    c2 = browser(on)
    gate2 = on.state.ctx.repo_for("ws_test_society").add_camera(Camera(None, "Gate", "stream", "rtsp://g",
                                                                       purpose="main gate"))
    assert c2.post("/api/lookouts", json={"camera_ids": [gate2.id], "description": "a red shirt"}).status_code == 200
