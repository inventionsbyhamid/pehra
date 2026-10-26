import os
import secrets
from pathlib import Path

# DATA_KEY is required; tests get a key of their own, made here
os.environ.setdefault("DATA_KEY", secrets.token_hex(32))
# processes a test starts (python -c, python -m, spawned camera and scan processes) import the package from this
# checkout, whether or not PYTHONPATH was set by the runner
_BACKEND = str(Path(__file__).resolve().parents[1])
os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(filter(None, [_BACKEND, os.environ.get("PYTHONPATH")])))
