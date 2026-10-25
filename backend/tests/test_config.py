import secrets
from pathlib import Path

import cv2
import pytest
from pydantic import ValidationError

from watchful.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def make(monkeypatch, **env):
    monkeypatch.setenv("SESSION_SECRET", "secret")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_opencv_is_5():
    assert cv2.__version__.startswith("5.")


def test_settings_defaults(monkeypatch):
    s = make(monkeypatch)
    assert s.storage == "local"
    assert s.ai_provider == "openrouter"
    assert s.analysis_fps == 5.0
    assert s.detector == "yolox_nano"


def test_settings_env_override(monkeypatch):
    assert make(monkeypatch, STORAGE="s3").storage == "s3"


def test_available_cpus_reads_the_container_limit(tmp_path):
    from watchful.config import available_cpus
    f = tmp_path / "cpu.max"
    f.write_text("600000 100000\n")
    assert available_cpus(f) == 6
    f.write_text("max 100000\n")
    import os
    assert available_cpus(f) == os.cpu_count()


def test_a_blank_or_short_data_key_stops_the_app(monkeypatch):
    """A blank DATA_KEY used to store every camera address, NVR passwords included, unencrypted."""
    for bad in ("", "   ", "a short key"):
        with pytest.raises(ValidationError, match=r"DATA_KEY is .*secrets.token_hex\(32\)"):
            make(monkeypatch, DATA_KEY=bad)
    with pytest.raises(ValidationError, match="DATA_KEY_OLD is only 9 characters"):
        make(monkeypatch, DATA_KEY=secrets.token_hex(32), DATA_KEY_OLD="too short")
    old = [secrets.token_hex(32), "x" * 44]
    s = make(monkeypatch, DATA_KEY=secrets.token_hex(32), DATA_KEY_OLD=",".join(old))
    assert s.data_key_old == old and make(monkeypatch, DATA_KEY_OLD="").data_key_old == []
    if (ROOT / ".env.example").exists():  # copied as shipped, it does not start
        monkeypatch.delenv("DATA_KEY")
        with pytest.raises(ValidationError, match="DATA_KEY is blank"):
            Settings(_env_file=ROOT / ".env.example")


def test_a_blank_session_secret_stops_the_app(monkeypatch):
    for bad in ("", "  "):
        with pytest.raises(ValidationError, match="SESSION_SECRET is blank"):
            make(monkeypatch, SESSION_SECRET=bad)
