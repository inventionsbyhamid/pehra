import base64
import hashlib
import hmac
import shutil
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from watchful.registry import register

MEDIA_PATH = "/api/blobs/"


def society_key(workspace_id: str | None, *parts) -> str:
    """A blob key under its society, `{workspace_id}/...`, so storage, links and retention can tell societies apart.
    Without a society (a test's bare camera) the parts alone."""
    return "/".join([str(workspace_id)] * bool(workspace_id) + [str(p) for p in parts])


def in_society(key: str | None, workspace_id: str) -> bool:
    return bool(key) and bool(workspace_id) and key.startswith(f"{workspace_id}/")


class BlobStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def url(self, key: str) -> str: ...
    def delete(self, key: str) -> None: ...
    def delete_older_than(self, days: int, keep: tuple[str, ...] = ("people/",)) -> int: ...


class SocietyBlobs:
    """One society's view of the blob store: a key outside `{workspace_id}/` is refused, so a camera, an AI check or a
    search of one society can neither write nor read another's pictures."""

    def __init__(self, inner: BlobStore, workspace_id: str):
        if not workspace_id:
            raise ValueError("SocietyBlobs needs the society's workspace_id")
        self.inner, self.workspace_id = inner, workspace_id

    def _check(self, key: str) -> str:
        if not in_society(key, self.workspace_id):
            raise KeyError(f"{key} is not a key of society {self.workspace_id}")
        return key

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.inner.put(self._check(key), data, content_type)

    def get(self, key: str) -> bytes:
        return self.inner.get(self._check(key))

    def url(self, key: str) -> str:
        return self.inner.url(self._check(key))

    def delete(self, key: str) -> None:
        self.inner.delete(self._check(key))

    def __getattr__(self, name):
        return getattr(self.inner, name)


class BackgroundPuts:
    """Writes go to a few threads so a scan worker never waits on storage (S3 PUTs); flush() before finishing."""

    def __init__(self, inner: BlobStore, threads: int = 4):
        from concurrent.futures import ThreadPoolExecutor
        self.inner = inner
        self.pool = ThreadPoolExecutor(threads)
        self.pending = []

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.pending.append(self.pool.submit(self.inner.put, key, data, content_type))

    def get(self, key: str) -> bytes:
        return self.inner.get(key)

    def url(self, key: str) -> str:
        return self.inner.url(key)

    def delete(self, key: str) -> None:
        self.inner.delete(key)

    def flush(self) -> None:
        for f in self.pending:
            f.result()
        self.pending.clear()
        self.pool.shutdown()


class MediaSigner:
    """Short lived links to local pictures and clips, as S3's presigned links are: the key and an expiry, signed."""

    def __init__(self, secret: str, ttl_s: int):
        self._key = hashlib.sha256(b"media:" + secret.encode()).digest()
        self.ttl_s = ttl_s

    def _sig(self, key: str, expires: int) -> str:
        mac = hmac.new(self._key, f"{key}\n{expires}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac[:18]).decode()

    def url(self, key: str, now: float | None = None) -> str:
        expires = int((now or time.time()) + self.ttl_s)
        return f"{MEDIA_PATH}{quote(key)}?e={expires}&s={self._sig(key, expires)}"

    def valid(self, key: str, expires: int, sig: str, now: float | None = None) -> bool:
        return expires > (now or time.time()) and hmac.compare_digest(self._sig(key, expires), sig or "")


@register("blobs", "local")
class LocalBlobStore:
    def __init__(self, data_dir: str | Path, name: str = "local", signer: MediaSigner | None = None, **_):
        self.root = Path(data_dir) / "blobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.signer = signer  # served by api/blobs.py; without one (tests, tools) links are unsigned

    def _path(self, key: str) -> Path:
        if ".." in Path(key).parts or key.startswith("/"):
            raise ValueError(f"bad key {key}")
        return self.root / key

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def url(self, key: str) -> str:
        return self.signer.url(key) if self.signer else f"{MEDIA_PATH}{quote(key)}"

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> int:
        """Every blob under `prefix` (a society's `{workspace_id}/`, when the demo reset deletes a sandbox)."""
        if not prefix.endswith("/"):
            raise ValueError("a prefix ends with /")
        folder = self._path(prefix.rstrip("/"))
        removed = sum(1 for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else 0
        if folder.is_dir():
            shutil.rmtree(folder)
        return removed

    def delete_older_than(self, days: int, keep: tuple[str, ...] = ("people/",)) -> int:
        """Retention; enrolment photos stay until the person is deleted."""
        cutoff = time.time() - days * 86400
        removed = 0
        for path in self.root.rglob("*"):
            if path.relative_to(self.root).as_posix().startswith(keep):
                continue
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        return removed

