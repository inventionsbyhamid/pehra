"""Which OpenCV this Python loads, whether it is COOL's Graviton build, and whether it decodes camera video: prints
cv2's version and file, the lines of cv2.getBuildInformation() that show the build (KleidiCV is Arm's HAL that COOL is
built with), its video backends, and a decode of each clip in deploy/clips.

    python deploy/cv2_info.py                      prints, never fails
    python deploy/cv2_info.py --require-kleidicv   exits 3 when KleidiCV is not in the build
    python deploy/cv2_info.py --require-video      exits 5 unless FFMPEG is a video backend and every clip decodes
    python deploy/cv2_info.py --require-cool       exits 3, then 4 when cv2 is not loaded from under COOL_ROOT, then 5
    python deploy/cv2_info.py --require-stock      exits 6 unless OpenCV 5, 7 unless arm64, then 5 (stock, no COOL)

KleidiCV alone does not prove COOL: stock OpenCV builds for Arm can carry it too. The file the module was loaded
from does: on the COOL image it lies under /opt/cool (COOL_ROOT to change it).

Cameras and NVRs stream H.264 or H.265 over RTSP, and recordings people upload are the same codecs in MP4; both go
through cv2.VideoCapture's FFmpeg backend. The clips are two seconds of 160x120 at 15 fps, made with
    ffmpeg -f lavfi -i testsrc2=size=160x120:rate=15 -t 2 -c:v libx264 -crf 36 -pix_fmt yuv420p -profile:v baseline
           -movflags +faststart h264.mp4
    ffmpeg -f lavfi -i testsrc2=size=160x120:rate=15 -t 2 -c:v libx265 -crf 36 -pix_fmt yuv420p -tag:v hvc1 hevc.mp4"""
import os
import platform
import re
import sys
from pathlib import Path

import cv2

SHOWN = re.compile(r"kleidicv|cool|version control|^\s*hal|cpu/hw features|baseline|dispatched|parallel framework|"
                   r"platform:|host:|python 3:|interpreter:|install to:|video i/o|ffmpeg|avcodec|avformat", re.I)
COOL_ROOT = "/opt/cool"
CLIPS = Path(__file__).resolve().parent / "clips"
VIDEO = {"h264.mp4": "H.264 MP4", "hevc.mp4": "H.265 MP4"}
CLIP_FRAMES, CLIP_SIZE = 30, (120, 160)


def video(module=cv2, clips: Path = CLIPS) -> bool:
    """FFMPEG among the video backends, and each clip read to its end, every frame decoded."""
    registry = module.videoio_registry
    names = [registry.getBackendName(b) for b in registry.getBackends()]
    print(f"video backends: {', '.join(dict.fromkeys(names)) or 'none'}")
    ok = "FFMPEG" in names
    if not ok:
        print("FFMPEG is not among the video backends: camera streams and recordings would not open")
    for name, what in VIDEO.items():
        cap = module.VideoCapture(str(clips / name))
        frames = 0
        while cap.isOpened():
            read, image = cap.read()
            if not read:
                break
            frames += image.shape[:2] == CLIP_SIZE
        good = frames == CLIP_FRAMES
        print(f"video {name} ({what}): {frames} of {CLIP_FRAMES} frames decoded, {'ok' if good else 'FAIL'}")
        ok = ok and good
    return ok


def main(argv: list[str], module=cv2) -> int:
    info = module.getBuildInformation()
    root = os.path.realpath(os.environ.get("COOL_ROOT", COOL_ROOT))
    path = os.path.realpath(module.__file__)
    print(f"cv2 {module.__version__} from {module.__file__}")
    for line in info.splitlines():
        if SHOWN.search(line):
            print(line.rstrip())
    kleidicv = "kleidicv" in info.lower()
    cool = os.path.commonpath([root, path]) == root
    print(f"KleidiCV {'loaded' if kleidicv else 'not in this build'}")
    print(f"COOL {'yes' if cool else 'no'}: cv2 {'is' if cool else 'is not'} loaded from under {root}")
    print(f"opencv build: {'COOL' if cool else 'stock OpenCV, not COOL'}")
    machine = platform.machine()
    print(f"machine: {machine}")
    decodes = video(module)
    if "--require-stock" in argv:
        if not str(module.__version__).startswith("5."):
            return 6
        if machine not in ("aarch64", "arm64"):
            return 7
        return 0 if decodes else 5
    if ("--require-kleidicv" in argv or "--require-cool" in argv) and not kleidicv:
        return 3
    if "--require-cool" in argv and not cool:
        return 4
    if ("--require-video" in argv or "--require-cool" in argv) and not decodes:
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
