"""Download models and sample clips, verifying sha256 when known. With --demo, only the demo cameras' stock clips
(vision/models.py DEMO_CLIPS): each checked, then cut to its camera's view with ffmpeg."""
import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from watchful.vision.models import (DEMO_CLIPS, EMBEDDERS, MODEL_EXTRAS, MODEL_FILES, SAMPLES,  # noqa: E402
                                    StockClip, embedder_path, extra_path, model_path)


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
    if url.startswith("repo:"):  # a file kept in this repo
        shutil.copyfile(ROOT / url.removeprefix("repo:"), tmp)
    else:
        urllib.request.urlretrieve(url, tmp)
    tmp.rename(dest)


def fetch_clip(name: str, clip: StockClip, data: Path) -> bool:
    """The stock file checked by SHA 256, then cropped and scaled; the file as downloaded is not kept."""
    dest = data / "samples" / name
    if dest.exists():
        print(f"{name}: already there, {sha256(dest)}")
        return True
    tools = {t: shutil.which(t) for t in ("curl", "ffmpeg")}
    if not all(tools.values()):
        print(f"{name}: needs {' and '.join(t for t, p in tools.items() if not p)}")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = dest.with_name(dest.stem + ".source" + dest.suffix)
    part = dest.with_name(dest.stem + ".part" + dest.suffix)
    try:
        # the Pexels file server refuses Python's own client
        subprocess.run([tools["curl"], "-fsSL", "-o", str(source), clip.url], check=True)
        got = sha256(source)
        if got != clip.sha256:
            print(f"{name}: sha256 mismatch {got}")
            return False
        subprocess.run([tools["ffmpeg"], "-v", "error", "-y", "-i", str(source), "-vf", clip.vf, "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(part)], check=True)
        part.rename(dest)
    finally:
        source.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
    print(f"{name}: {clip.page} by {clip.creator}, checked {clip.sha256}")
    return True


def main(argv: list[str] | None = None) -> int:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--demo", action="store_true", help="only the demo cameras' stock clips (needs curl and ffmpeg)")
    args.add_argument("--data", type=Path, default=Path("data"), help="the data folder the clips go under")
    opts = args.parse_args(argv)
    if opts.demo:
        return 0 if all([fetch_clip(name, clip, opts.data) for name, clip in DEMO_CLIPS.items()]) else 1
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
        dest = opts.data / "samples" / name
        fetch(url, dest)
        print(f"{name}: {sha256(dest)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
