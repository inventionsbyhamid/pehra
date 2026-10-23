import json

import pytest
from pydantic import ValidationError

from watchful.tuning import Tuning, load_tuning


def test_defaults():
    t = Tuning()
    assert t.falls.hold_s == 2.0 and t.faces.cosine == 0.363


def test_file_override(tmp_path):
    f = tmp_path / "tuning.json"
    f.write_text(json.dumps({"falls": {"hold_s": 3.5}}))
    t = load_tuning(f)
    assert t.falls.hold_s == 3.5
    assert t.falls.lying_angle == Tuning().falls.lying_angle


def test_camera_override_merges(tmp_path):
    f = tmp_path / "tuning.json"
    f.write_text(json.dumps({"motion": {"var_threshold": 40}, "falls": {"hold_s": 3.0}}))
    t = load_tuning(f, {"falls": {"hold_s": 4.0}})
    assert t.falls.hold_s == 4.0 and t.motion.var_threshold == 40


def test_unknown_key_rejected():
    with pytest.raises(ValidationError):
        load_tuning(None, {"falls": {"hold_seconds": 4.0}})
