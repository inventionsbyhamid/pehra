import pytest

from accounts import society_repo
from watchful import components
from watchful.config import Settings
from watchful.registry import available
from watchful.tuning import Tuning


def settings(**kw):
    return Settings(demo_password="x", session_secret="y", _env_file=None, **kw)


def test_every_kind_lists_its_choices():
    components.load_all()
    assert {"yolox_nano", "yolox_tiny", "yolox_s"} <= set(available("detector"))
    assert "mediapipe" in available("pose") and "yunet_sface" in available("faces")
    assert "siglip2" in available("embedder")
    assert {"openrouter", "bedrock"} <= set(available("llm"))
    assert "local" in available("blobs") and "sqlite" in available("repo") and "log" in available("notify")


def test_settings_pick_the_detector():
    det = components.make_detector(settings(detector="yolox_tiny"), Tuning())
    assert det.size == 416 and det.name == "yolox_tiny"


def test_unknown_choice_says_what_exists():
    with pytest.raises(ValueError, match="yolox_nano"):
        components.make_detector(settings(detector="yolo_v99"), Tuning())


def test_task_models_override_the_default(tmp_path):
    s = settings(openrouter_api_key="k", ai_model_compile="some/other-model", data_dir=tmp_path)
    repo = society_repo(s)
    assert components.make_llm(s, repo, "compile").inner.model == "some/other-model"
    assert components.make_llm(s, repo, "verify").inner.model == s.openrouter_model
