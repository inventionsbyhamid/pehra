import os
import secrets

# DATA_KEY is required; tests get a key of their own, made here
os.environ.setdefault("DATA_KEY", secrets.token_hex(32))
