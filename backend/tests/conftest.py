import os
import secrets
from pathlib import Path

import pytest

# DATA_KEY is required; tests get a key of their own, made here
os.environ.setdefault("DATA_KEY", secrets.token_hex(32))
# processes a test starts (python -c, python -m, spawned camera and scan processes) import the package from this
# checkout, whether or not PYTHONPATH was set by the runner
_BACKEND = str(Path(__file__).resolve().parents[1])
os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(filter(None, [_BACKEND, os.environ.get("PYTHONPATH")])))


@pytest.fixture(autouse=True, scope="module")
def _one_text_tower_at_a_time():
    """The image model's text tower is about 1.5 GB, and compiling or saving any look text loads it (the age guard's
    meaning check): a module that loaded it gives it back, so the suite never holds it from module to module."""
    yield
    from watchful.components import made_embedders
    for emb in made_embedders():
        getattr(emb, "release_text", lambda: False)()
