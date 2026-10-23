import time
from pathlib import Path
from typing import Protocol

from watchful.registry import register


class BlobStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def url(self, key: str) -> str: ...
    def delete(self, key: str) -> None: ...
    def delete_older_than(self, days: int, keep: tuple[str, ...] = ("people/",)) -> int: ...


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


@register("blobs", "local")
class LocalBlobStore:
    def __init__(self, data_dir: str | Path, name: str = "local", **_):
        self.root = Path(data_dir) / "blobs"
        self.root.mkdir(parents=True, exist_ok=True)

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
        return f"/blobs/{key}"

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

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

