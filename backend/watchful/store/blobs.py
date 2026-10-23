import time
from pathlib import Path
from typing import Protocol


class BlobStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def url(self, key: str) -> str: ...
    def delete_older_than(self, days: int) -> int: ...


class LocalBlobStore:
    def __init__(self, data_dir: str | Path):
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

    def delete_older_than(self, days: int) -> int:
        cutoff = time.time() - days * 86400
        removed = 0
        for path in self.root.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        return removed


def make_blobs(settings) -> BlobStore:
    return LocalBlobStore(settings.data_dir)
