import cv2

from watchful.config import Settings


def make(monkeypatch, **env):
    monkeypatch.setenv("DEMO_PASSWORD", "pw")
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
