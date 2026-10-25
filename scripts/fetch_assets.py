"""Download models and sample clips, verifying sha256 when known."""
import hashlib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from watchful.vision.models import (EMBEDDERS, MODEL_EXTRAS, MODEL_FILES, SAMPLES, embedder_path,  # noqa: E402
                                    extra_path, model_path, sample_path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path) -> None:
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)


def main() -> int:
    bad = 0
    for name, m in MODEL_FILES.items():
        dest = model_path(name)
        fetch(m.url, dest)
        digest = sha256(dest)
        if m.sha256 and m.sha256 != digest:
            print(f"{name}: sha256 mismatch {digest}")
            bad += 1
        else:
            print(f"{name}: {digest} {dest.stat().st_size // 1024} KB")
    for name, files in MODEL_EXTRAS.items():
        for file, (url, want) in files.items():
            dest = extra_path(file)
            fetch(url, dest)
            if sha256(dest) != want:
                print(f"{name} {file}: sha256 mismatch {sha256(dest)}")
                bad += 1
    for name, spec in EMBEDDERS.items():
        for file, url in spec.files.items():
            fetch(url, embedder_path(name, file))
        print(f"{name}: {', '.join(spec.files)}")
    for name, url in SAMPLES.items():
        dest = sample_path(name)
        fetch(url, dest)
        print(f"{name}: {sha256(dest)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
