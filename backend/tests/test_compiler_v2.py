import json

from watchful.agent.compiler import CompileContext, compile_watch, mentions_age
from watchful.agent.llm import FakeLLM
from watchful.agent.prompting import render
from watchful.rules.presets import purposes, suggestions
from watchful.rules.signals import REGISTRY
from watchful.rules.watch import select_watches

POOL = CompileContext(zones={"water": "water", "pool deck": "area", "lifeguard chair": "area"}, lines=[],
                      state_examples={}, people={}, camera_flags={"night_ir": True}, purpose="pool")
GATE = CompileContext(zones={"lobby": "area", "gate area": "area"}, lines=["gate"],
                      state_examples={"gate area": {"closed": ["day"]}},
                      people={"stf_1": "Sunita"}, camera_flags={"night_ir": True, "plate_readable": False},
                      purpose="main gate")


def reply(watch, coverage=(), setup=(), cannot=()):
    return json.dumps({"watch": watch, "coverage": list(coverage), "setup": list(setup), "cannot": list(cannot)})


def test_cannot_offers_the_nearest_alternative():
    age = reply({"name": "Child alone at the pool", "subject": {"classes": ["person"]},
                 "conditions": [{"signal": "in_zone", "zones": ["pool deck"]},
                                {"signal": "looks_like", "params": {"text": "a small child", "not_text": "an adult"}},
                                {"signal": "alone", "zones": ["pool deck"]}],
                 "severity": "critical", "verify": "Is a child alone at the pool?"},
                coverage=[{"clause": "a child", "signal": "looks_like", "tier": "B", "caveat": ""},
                          {"clause": "alone at the pool", "signal": "alone", "tier": "A", "caveat": ""}])
    r = compile_watch("tell me when a child is alone at the pool", POOL, FakeLLM([age]))
    assert r.error is None and r.cannot
    assert all(c.alternative and not mentions_age(c.alternative) for c in r.cannot)
    assert any(mentions_age(c.clause) for c in r.cannot)
    assert r.watch is not None  # the parts that are not about age stay: someone alone at the pool deck
    assert [c.signal for c in r.watch.conditions] == ["in_zone", "alone"]
    assert not mentions_age(json.dumps(r.watch.model_dump()))
    assert r.watch.name == "Someone alone at the pool" and r.watch.verify == "Is someone alone at the pool?"
    assert not any(mentions_age(c.clause) for c in r.coverage)


def test_a_request_about_age_is_a_cannot_even_when_the_ai_misses_it():
    sneaky = reply({"name": "Small person at the water", "subject": {"classes": ["person"]},
                    "conditions": [{"signal": "in_zone", "zones": ["water"]}], "verify": "Is a small person there?"})
    r = compile_watch("alert me if a kid goes near the water", POOL, FakeLLM([sneaky]))
    assert r.cannot and all(not mentions_age(c.alternative) for c in r.cannot)
    assert r.watch is None or not mentions_age(json.dumps(r.watch.model_dump()))


def test_what_cannot_be_watched_is_never_a_silent_no_op():
    r = compile_watch("tell me when someone is sad", POOL, FakeLLM([reply(None)]))
    assert r.watch is None and len(r.cannot) == 1 and r.cannot[0].alternative and r.cannot[0].reason
    told = reply(None, cannot=[{"clause": "someone is sad", "reason": "Feelings can't be seen reliably.",
                                "alternative": "someone sitting alone in one place for 20 minutes"}])
    r = compile_watch("tell me when someone is sad", POOL, FakeLLM([told]))
    assert r.cannot[0].alternative == "someone sitting alone in one place for 20 minutes"
    unknown = reply({"name": "x", "conditions": [{"signal": "mood", "params": {}}], "verify": "?"})
    r = compile_watch("tell me when someone is sad", POOL, FakeLLM([unknown] * 3))
    assert r.watch is None and r.cannot and "mood" not in r.cannot[0].alternative


def test_site_watch_covers_new_cameras(tmp_path):
    site = reply({"name": "Camera covered", "scope": "site", "subject": {"classes": []},
                  "conditions": [{"signal": "camera_health", "op": "!=", "value": "live"}],
                  "severity": "warning", "verify": ""})
    r = compile_watch("tell me when any camera is covered or offline", GATE, FakeLLM([site]))
    assert r.watch.scope == "site"

    from watchful.store.db import Camera, SqliteRepository, WatchRow
    repo = SqliteRepository(tmp_path / "w.db")
    gate, pool = (repo.add_camera(Camera(None, n, "stream", None)) for n in ("Gate", "Pool"))
    spec = r.watch.model_dump()
    site_row = repo.add_watch(WatchRow(None, gate.id, "covered", spec))
    pool_row = repo.add_watch(WatchRow(None, pool.id, "pool only", {**spec, "scope": "camera"}))
    repo.add_watch(WatchRow(None, gate.id, "off", spec, enabled=False))
    later = repo.add_camera(Camera(None, "Added later", "stream", None))

    def running(cam):
        return [i for i, _ in select_watches(repo.list_watches(cam.id), cam.id, cam.zones)[0]]

    assert running(later) == [site_row.id]
    assert running(pool) == [site_row.id, pool_row.id]
    site_row.watch = {**spec, "excluded_cameras": [pool.id]}
    repo.update_watch(site_row)
    assert running(pool) == [pool_row.id] and running(later) == [site_row.id]


def test_coverage_tiers_are_reported():
    cab = reply({"name": "Cab log", "subject": {"classes": ["car"]},
                 "conditions": [{"signal": "crossed", "params": {"line": "gate", "direction": "any"}},
                                {"signal": "attribute", "params": {"name": "plate_colour"}, "op": "is",
                                 "value": "yellow"}],
                 "output": "log", "severity": "warning", "verify": ""},
                coverage=[{"clause": "every cab", "signal": "attribute", "tier": "A", "caveat": ""}])
    r = compile_watch("log every cab in and out", GATE, FakeLLM([cab]))
    tiers = {c.signal: c for c in r.coverage}
    assert tiers["crossed"].tier == "A" and tiers["attribute"].tier == "B"
    assert "night" in tiers["attribute"].caveat.lower()
    assert r.watch.output == "log" and r.watch.severity == "info"
    assert all(c.tier in ("A", "B", "C", "X") for c in r.coverage)


def test_a_never_seen_event_compiles_into_generic_blocks():
    ladder = reply({"name": "Ladder near the terrace door", "subject": {"classes": ["person"]},
                    "conditions": [{"signal": "in_zone", "zones": ["terrace door"]},
                                   {"signal": "looks_like", "params": {"text": "a person carrying a ladder",
                                                                       "not_text": "a person not carrying anything",
                                                                       "hold_s": 2}}],
                    "time_window": {"start": "20:00", "end": "06:00"}, "severity": "warning",
                    "verify": "Is this person carrying a ladder near the terrace door?"})
    ctx = CompileContext(zones={"terrace door": "area"}, lines=[], state_examples={}, people={}, camera_flags={},
                         purpose="perimeter")
    r = compile_watch("someone carrying a ladder near the terrace door after 8 PM", ctx, FakeLLM([ladder]))
    w = r.watch
    assert r.error is None and not r.cannot and not r.setup
    assert w.subject.classes == ["person"] and w.time_window.start == "20:00"
    assert [c.signal for c in w.conditions] == ["in_zone", "looks_like"]
    assert all(c.signal in REGISTRY for c in w.conditions)
    assert {c.signal: c.tier for c in r.coverage} == {"in_zone": "A", "looks_like": "B"}


def test_missing_zones_lines_and_examples_become_setup_tasks():
    gate_open = reply({"name": "Gate open late", "subject": {"classes": []},
                       "conditions": [{"signal": "zone_state", "zones": ["Gate Area"], "op": "is", "value": "open",
                                       "params": {"hold_s": 120}}],
                       "time_window": {"start": "23:00", "end": "06:00"}, "severity": "warning", "verify": "?"})
    r = compile_watch("gate left open after 11 PM", GATE, FakeLLM([gate_open]))
    assert r.watch.conditions[0].zones == ["gate area"]  # spelling fixed to the drawn zone
    assert any("open" in s and "gate area" in s for s in r.setup)
    assert any("night" in s for s in r.setup)
    lane = reply({"name": "Parked in the fire lane", "subject": {"classes": ["car"]},
                  "conditions": [{"signal": "stationary_s", "zones": ["fire lane"], "op": ">=", "value": 600}],
                  "verify": "?"})
    r = compile_watch("a car parked in the fire lane for 10 minutes", GATE, FakeLLM([lane]))
    assert r.watch is not None and any("fire lane" in s for s in r.setup)
    line = reply({"name": "In at the side gate", "conditions": [{"signal": "crossed", "params": {"line": "side gate"}}],
                  "verify": "?"})
    r = compile_watch("someone comes in at the side gate", GATE, FakeLLM([line]))
    assert any("line" in s and "side gate" in s for s in r.setup)


def test_compiler_prompt_describes_building_blocks_and_privacy():
    llm = FakeLLM([reply(None)])
    compile_watch("anything", GATE, llm)
    prompt = llm.calls[0][0]
    for name in REGISTRY:
        if not name.startswith("_"):
            assert name in prompt
    for word in ("looks_like", "zone_looks_like", "coverage", "setup", "cannot", "alternative", "age", "gate",
                 "lobby", "Sunita", "main gate", "night"):
        assert word in prompt, word
    assert "uniform" not in json.dumps([s.name for s in REGISTRY.values()])
    assert "plate_colour" not in REGISTRY


def test_old_callers_still_work():
    llm = FakeLLM([json.dumps({"watch": {"name": "Car enters", "subject": {"classes": ["car"]},
                                         "conditions": [{"signal": "entered", "zones": ["parking lot"]}],
                                         "verify": "?"}, "problem": None})])
    r = compile_watch("car enters the parking lot", ["parking lot"], llm)
    assert r.problem is None and r.watch.conditions[0].signal == "entered"


def test_purposes_only_suggest_plain_watch_texts():
    data = purposes()
    assert {"pool", "main gate", "service gate", "parking", "lobby", "perimeter", "other"} <= set(data)
    assert "someone still in the water for 20 seconds" in suggestions("pool")
    assert "log every vehicle in and out" in suggestions("main gate")
    assert "log staff arriving and leaving" in suggestions("service gate")
    for name in data:
        for text in suggestions(name):
            assert not mentions_age(text) and "-" not in text and "—" not in text
    assert suggestions("no such purpose") == []
    text = render("compile", catalog=[], classes=[], vocabulary=[], zones={}, lines=[], state_examples={},
                  people={}, camera_flags={}, purpose="pool", presets=[])
    assert "pool" in text


def test_watch_card_api_previews_backtests_and_saves(tmp_path):
    import cv2
    import numpy as np
    from accounts import browser
    from test_api import StubManager
    from watchful.api import create_app
    from watchful.config import Settings

    s = Settings(session_secret="s" * 32, data_dir=tmp_path, _env_file=None)
    manager = StubManager()
    app = create_app(s, llm=FakeLLM([]), manager=manager)
    c = browser(app)
    pool = c.post("/api/cameras", json={"name": "Pool", "kind": "stream", "url": "rtsp://x"}).json()
    gate = c.post("/api/cameras", json={"name": "Gate", "kind": "stream", "url": "rtsp://y"}).json()
    zones = [{"id": "w1", "name": "water", "kind": "water", "polygon": [[0, 0.2], [1, 0.2], [1, 1], [0, 1]]},
             {"id": "l1", "name": "gate", "kind": "line", "polygon": [[0.5, 1], [0.5, 0]]}]
    assert c.patch(f"/api/cameras/{pool['id']}", json={"zones": zones}).status_code == 200
    assert [z["kind"] for z in c.get(f"/api/cameras/{pool['id']}").json()["zones"]] == ["water", "line"]

    still = reply({"name": "Still in the water", "subject": {"classes": ["person"]}, "severity": "critical",
                   "conditions": [{"signal": "still_s", "zones": ["water"], "op": ">=", "value": 20}], "verify": "?"})
    app.state.ctx.llm = FakeLLM([still])
    card = c.post(f"/api/cameras/{pool['id']}/watches/preview", json={"text": "someone still in the water"}).json()
    assert card["watch"]["name"] == "Still in the water" and card["coverage"][0]["tier"] == "B"
    assert card["backtest"]["assumed"] == ["still_s"] and card["backtest"]["fires"] == 0
    assert c.get(f"/api/cameras/{pool['id']}/watches").json() == []  # a preview saves nothing

    saved = c.post(f"/api/cameras/{pool['id']}/watches", json={"text": "someone still in the water",
                                                              "watch": card["watch"], "coverage": card["coverage"]})
    assert saved.status_code == 200 and saved.json()["id"]
    child = {**card["watch"], "name": "A child still in the water"}
    assert c.post(f"/api/cameras/{pool['id']}/watches", json={"watch": child}).status_code == 400

    site = reply({"name": "Camera covered", "scope": "site", "conditions": [
        {"signal": "camera_health", "op": "!=", "value": "live"}], "severity": "warning", "verify": ""})
    app.state.ctx.llm = FakeLLM([site])
    manager.started.clear()
    r = c.post(f"/api/cameras/{pool['id']}/watches", json={"text": "any camera covered"}).json()
    assert r["watch"]["scope"] == "site" and set(manager.started) == {pool["id"], gate["id"]}
    assert [w["text"] for w in c.get(f"/api/cameras/{gate['id']}/watches").json()] == ["any camera covered"]

    assert "pool" in c.get("/api/purposes").json()
    jpg = cv2.imencode(".jpg", np.full((60, 80, 3), (20, 120, 200), np.uint8))[1].tobytes()
    ex = c.post(f"/api/cameras/{pool['id']}/zones/w1/examples", data={"state": "Covered"},
                files={"file": ("p.jpg", jpg, "image/jpeg")})
    assert ex.status_code == 200 and ex.json()["state"] == "covered" and ex.json()["period"] == "day"
    assert [e["state"] for e in c.get(f"/api/cameras/{pool['id']}/examples").json()] == ["covered"]
    assert c.post(f"/api/cameras/{pool['id']}/zones/l1/examples", data={"state": "x"}).status_code == 404


def test_only_words_asking_for_a_log_make_one_and_a_watched_part_is_not_a_cannot():
    past = reply({"name": "Delivery past the lobby", "output": "log", "severity": "warning", "verify": "?",
                  "conditions": [{"signal": "crossed", "params": {"line": "gate", "direction": "in"}},
                                 {"signal": "looks_like", "params": {"text": "a delivery rider with a big bag",
                                                                     "not_text": "a person in everyday clothes"}}]},
                 coverage=[{"clause": "a delivery person", "signal": "looks_like", "tier": "B", "caveat": ""}],
                 cannot=[{"clause": "a delivery person", "reason": "Residents in the same shirt look alike.",
                          "alternative": "x"}])
    r = compile_watch("a delivery person goes past the lobby", GATE, FakeLLM([past.replace('"warning"', '"info"')]))
    assert r.watch.output == "alert" and r.watch.severity == "warning"
    assert r.cannot == [] and "look alike" in next(c for c in r.coverage if c.signal == "looks_like").caveat
    spam = json.loads(past)
    spam["watch"]["repeat"] = True
    r = compile_watch("keep a register of deliveries past the lobby", GATE, FakeLLM([json.dumps(spam)]))
    assert r.watch.output == "log" and r.watch.severity == "info" and not r.watch.repeat


def test_conditions_that_can_never_all_hold_are_sent_back():
    both = {"name": "Covered or offline", "verify": "?", "conditions": [
        {"signal": "camera_health", "op": "is", "value": "covered"},
        {"signal": "camera_health", "op": "is", "value": "offline"}]}
    llm = FakeLLM([reply(both), reply({**both, "combine": "any"})])
    r = compile_watch("any camera covered or offline", GATE, llm)
    assert r.watch.combine == "any" and "at once" in llm.calls[1][1][-1].text
