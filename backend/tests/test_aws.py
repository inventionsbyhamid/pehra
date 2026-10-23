import time

import boto3
import pytest
from moto import mock_aws

from watchful.components import make_blobs, make_repo
from watchful.config import Settings
from watchful.metrics import CloudWatchMetrics
from watchful.notify import SnsNotifier
from watchful.store.db import AlertRow, Camera, EventRow, WatchRow
from watchful.vision.faces import Enrollment
from watchful.vision.zones import Zone

REGION = "us-east-1"


@pytest.fixture
def aws(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        boto3.client("s3", region_name=REGION).create_bucket(Bucket="wf-test")
        yield


def settings(tmp_path, **kw):
    return Settings(demo_password="x", session_secret="y", data_dir=tmp_path, aws_region=REGION,
                    s3_bucket="wf-test", ddb_table_prefix="wft", _env_file=None, **kw)


@pytest.fixture(params=["sqlite", "dynamodb"])
def repo(request, tmp_path, aws):
    return make_repo(settings(tmp_path, db=request.param))


def ev(cam, ts, zone="kitchen", kind="enter"):
    return EventRow(None, cam, ts, kind, 1, "person", zone, {"dwell_s": 3.5}, "k.jpg", [0.1, 0.2], None, "Sana")


def test_repository_contract(repo):
    cam = repo.add_camera(Camera(None, "Gate", "stream", "rtsp://x", [Zone("z1", "gate", [(0, 0), (1, 0), (1, 1)])],
                                 True, {"falls": {"hold_s": 3}}))
    assert repo.get_camera(cam.id).zones[0].polygon[1] == (1, 0)
    cam.name = "Front gate"
    repo.update_camera(cam)
    assert [c.name for c in repo.list_cameras()] == ["Front gate"]

    repo.add_events([ev(cam.id, 10.5), ev(cam.id, 20, zone="door"), ev(cam.id, 30), ev("other", 15)])
    rows = repo.query_events(camera_id=cam.id, zone="kitchen", since=5, until=25)
    assert [r.ts for r in rows] == [10.5] and rows[0].data == {"dwell_s": 3.5} and rows[0].signature == [0.1, 0.2]
    assert repo.get_event(rows[0].id).person_label == "Sana"
    assert len(repo.query_events(kinds=["enter"])) == 4

    w = repo.add_watch(WatchRow(None, cam.id, "x", {"name": "x"}, True))
    w.enabled = False
    repo.update_watch(w)
    assert repo.list_watches(cam.id)[0].enabled is False

    a = repo.add_alert(AlertRow(None, cam.id, w.id, time.time(), "critical", "alert", "Fell", None, None, [], None, "fp1"))
    assert repo.alert_fingerprint_exists("fp1", 60) and not repo.alert_fingerprint_exists("fp2", 60)
    a.feedback = "up"
    repo.update_alert(a)
    assert repo.get_alert(a.id).feedback == "up" and repo.list_alerts(cam.id)[0].id == a.id

    repo.add_enrollment(Enrollment("p1", "Sana", [0.5] * 4))
    assert [e.name for e in repo.list_enrollments()] == ["Sana"]
    repo.delete_enrollment("p1")
    assert repo.list_enrollments() == []
    repo.record_ai_call()
    repo.record_ai_call()
    assert repo.count_ai_calls_today() == 2
    repo.set_person_label(cam.id, "g1", "Sana")
    assert repo.person_labels(cam.id) == {"g1": "Sana"}
    assert repo.delete_events_before(25) == 3


def test_s3_blobs(tmp_path, aws):
    blobs = make_blobs(settings(tmp_path, storage="s3"))
    blobs.put("cam/kf/1.jpg", b"123", "image/jpeg")
    assert blobs.get("cam/kf/1.jpg") == b"123"
    assert "wf-test" in blobs.url("cam/kf/1.jpg") and "Signature" in blobs.url("cam/kf/1.jpg")


def test_sns_and_cloudwatch(aws):
    topic = boto3.client("sns", region_name=REGION).create_topic(Name="wf")["TopicArn"]
    SnsNotifier(topic, REGION).send("Fall", "Someone fell in the kitchen.")
    m = CloudWatchMetrics(REGION)
    m.put("FramesPerSecond", 5.0, {"Camera": "cam1"})
    m.flush()
    names = [x["MetricName"] for x in boto3.client("cloudwatch", region_name=REGION).list_metrics(
        Namespace="Watchful")["Metrics"]]
    assert "FramesPerSecond" in names


def test_newest_rows_and_index_work(repo):
    repo.add_events([ev("cam9", float(t)) for t in range(10)])
    assert [r.ts for r in repo.query_events(camera_id="cam9", limit=3)] == [7.0, 8.0, 9.0]
    assert [r.ts for r in repo.query_events(limit=2)] == [8.0, 9.0]
    scene = EventRow(None, "cam9", 20.0, "scene", None, None, None, {}, "s.jpg", None, None, None)
    repo.add_events([scene])
    assert [e.id for e in repo.events_missing_embedding(["scene"])] == [scene.id]
    repo.set_event_embedding(scene.id, [0.5, 0.5])
    assert repo.events_missing_embedding(["scene"]) == [] and repo.get_event(scene.id).embedding == [0.5, 0.5]
    for i in range(5):
        repo.add_alert(AlertRow(None, "cam9", None, time.time() + i, "info", "alert", f"a{i}", None, None, [], None,
                                f"f{i}"))
    assert [a.summary for a in repo.list_alerts("cam9", limit=2)] == ["a4", "a3"]
    assert [a.summary for a in repo.list_alerts(limit=1)] == ["a4"]
    assert repo.delete_events_before(5) == 5
