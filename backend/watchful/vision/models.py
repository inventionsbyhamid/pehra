import logging
from dataclasses import dataclass
from pathlib import Path

import cv2

from watchful.config import Settings

log = logging.getLogger(__name__)

HF = "https://huggingface.co/opencv"
OUR_EXPORTS = "repo:assets/yolox"  # small exports of ours, kept in the repo; fetch_assets copies them
PADDLE = "https://huggingface.co/PaddlePaddle"


@dataclass(frozen=True)
class ModelFile:
    url: str
    filename: str
    license: str
    sha256: str | None = None


MODEL_FILES: dict[str, ModelFile] = {
    "yolox_nano": ModelFile(
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx",
        "yolox_nano.onnx", "Apache-2.0 (Megvii YOLOX)",
        "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"),
    # exported from the same nano weights at smaller inputs (scripts/export_yolox), kept in the repo
    "yolox_nano_320": ModelFile(
        f"{OUR_EXPORTS}/yolox_nano_320.onnx", "yolox_nano_320.onnx", "Apache-2.0 (Megvii YOLOX, exported)",
        "feb9d0f814e1ad06117711fc0b563ff708f5a893c58b7c3c14a7ed102b7d546d"),
    "yolox_nano_384": ModelFile(
        f"{OUR_EXPORTS}/yolox_nano_384.onnx", "yolox_nano_384.onnx", "Apache-2.0 (Megvii YOLOX, exported)",
        "d98dba6aaf544d9a75d1da912ad64552410c550cc85a81332cf10e84b91290d4"),
    "yolox_tiny": ModelFile(
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
        "yolox_tiny.onnx", "Apache-2.0 (Megvii YOLOX)",
        "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"),
    "yolox_s": ModelFile(
        f"{HF}/object_detection_yolox/resolve/main/object_detection_yolox_2022nov.onnx",
        "object_detection_yolox_2022nov.onnx", "Apache-2.0 (OpenCV Model Zoo)",
        "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"),
    "pose": ModelFile(
        f"{HF}/pose_estimation_mediapipe/resolve/main/pose_estimation_mediapipe_2023mar.onnx",
        "pose_estimation_mediapipe_2023mar.onnx", "Apache-2.0 (OpenCV Model Zoo, MediaPipe)",
        "9d89c599319a18fb7d2e28451a883476164543182bafca5f09eb2cf767ed2f3f"),
    "yunet": ModelFile(
        f"{HF}/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
        "face_detection_yunet_2023mar.onnx", "MIT (OpenCV Model Zoo)",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "sface": ModelFile(
        f"{HF}/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx",
        "face_recognition_sface_2021dec.onnx", "Apache-2.0 (OpenCV Model Zoo)",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
    # trained on Chinese plates; a plate it misses falls back to PP-OCR text boxes (vision/plates.py)
    "lpd_yunet": ModelFile(
        f"{HF}/license_plate_detection_yunet/resolve/main/license_plate_detection_lpd_yunet_2023mar.onnx",
        "license_plate_detection_lpd_yunet_2023mar.onnx", "Apache-2.0 (OpenCV Model Zoo, LPD-YuNet by watrix.ai)",
        "6d4978a7b6d25514d5e24811b82bfb511d166bdd8ca3b03aa63c1623d4d039c7"),
    "text_det": ModelFile(
        f"{HF}/text_detection_ppocr/resolve/main/text_detection_en_ppocrv3_2023may.onnx",
        "text_detection_en_ppocrv3_2023may.onnx", "Apache-2.0 (OpenCV Model Zoo, PaddleOCR PP-OCRv3 English DB)",
        "03f550c6b406fda8bf54bd8327815f6c7e2edd98cea02348c93d879254366587"),
    # grey 100x32 input; reads digits and lowercase letters
    "text_rec": ModelFile(
        f"{HF}/text_recognition_crnn/resolve/main/text_recognition_CRNN_EN_2021sep.onnx",
        "text_recognition_CRNN_EN_2021sep.onnx", "Apache-2.0 (OpenCV Model Zoo, CRNN English)",
        "a84b1f6e11a65c2d733cb0cc1f014aae3f99051e3f11447dc282faa678eee544"),
    # colour 48 px high input, any case; the classic engine cannot read it, and its 1 x T x C output is decoded by
    # hand (TextRecognitionModel returns an empty string for it); its symbols are in MODEL_EXTRAS
    "text_rec_v5": ModelFile(
        f"{PADDLE}/en_PP-OCRv5_mobile_rec_onnx/resolve/main/inference.onnx",
        "ppocrv5/en_rec_mobile.onnx", "Apache-2.0 (PaddleOCR PP-OCRv5 English mobile recognition, ONNX by Paddle)",
        "b5f833dfc5d0eb71da397b4efa06ebeee9b431b690a47d6af40d77d8eabc557f"),
}

# side files a model needs: local name to (url, sha256)
MODEL_EXTRAS: dict[str, dict[str, tuple[str, str]]] = {
    "text_rec_v5": {"ppocrv5/en_rec_mobile.yml": (
        f"{PADDLE}/en_PP-OCRv5_mobile_rec_onnx/resolve/main/inference.yml",
        "27e91d0582f40168aa218303c76e184bc78fa7a5d105aad0cfbad8458b441067")},
}

# symbols after the CTC blank at index 0; PP-OCR models keep theirs in a side file
CHARSETS = {"text_rec": "0123456789abcdefghijklmnopqrstuvwxyz"}

SIGLIP2 = "https://huggingface.co/onnx-community/siglip2-base-patch16-224-ONNX/resolve/main/"
SIGLIP2_B32 = "https://huggingface.co/onnx-community/siglip2-base-patch32-256-ONNX/resolve/main/"


@dataclass(frozen=True)
class EmbedderSpec:
    """Image and text towers in one space, run on the OpenCV DNN engine."""
    folder: str
    files: dict[str, str]  # local name to url
    license: str
    size: int
    mean: float
    std: float
    max_tokens: int
    pad_id: int
    lowercase: bool
    output: str
    logit_scale: float | None = None  # sigmoid calibration learned with the model: p = sigmoid(exp(scale) cos + bias)
    logit_bias: float | None = None


EMBEDDERS: dict[str, EmbedderSpec] = {
    "siglip2": EmbedderSpec(
        "siglip2",
        {"vision.onnx": SIGLIP2 + "onnx/vision_model.onnx", "text.onnx": SIGLIP2 + "onnx/text_model.onnx",
         "tokenizer.json": SIGLIP2 + "tokenizer.json"},
        "Apache-2.0 (Google SigLIP 2, ONNX by onnx-community)",
        224, 0.5, 0.5, 64, 0, True, "pooler_output", 4.724453449249268, -16.771724700927734),
    # 64 image tokens instead of 196: about 2.5 to 2.9 times faster, same text tower and tokenizer
    "siglip2_b32": EmbedderSpec(
        "siglip2_b32",
        {"vision.onnx": SIGLIP2_B32 + "onnx/vision_model.onnx", "text.onnx": SIGLIP2_B32 + "onnx/text_model.onnx",
         "tokenizer.json": SIGLIP2_B32 + "tokenizer.json"},
        "Apache-2.0 (Google SigLIP 2 base patch32 256, ONNX by onnx-community)",
        256, 0.5, 0.5, 64, 0, True, "pooler_output", 4.717002868652344, -16.766876220703125),
}

SAMPLE_BASE = "https://raw.githubusercontent.com/opencv/opencv/5.x/samples/data/"
SAMPLES = {name: SAMPLE_BASE + name for name in ("vtest.avi", "basketball1.png", "basketball2.png", "messi5.jpg")}

PEXELS_LICENSE = ("Pexels licence (pexels.com/license): free to use and modify, no attribution required; "
                  "not to be sold or redistributed unaltered")


@dataclass(frozen=True)
class StockClip:
    """A demo camera's clip from a stock site. Never committed: fetched from a pinned file, checked by SHA 256, then
    cropped and scaled with ffmpeg to the camera's view (`vf`)."""
    url: str
    sha256: str
    page: str
    creator: str
    license: str
    vf: str


# python scripts/fetch_assets.py --demo
DEMO_CLIPS: dict[str, StockClip] = {
    # a drone held still above an outdoor pool, adults only, 24.6 s; the camera keeps the busy right three fifths
    "demo/pool.mp4": StockClip(
        "https://videos.pexels.com/video-files/28559383/12420637_2560_1440_30fps.mp4",
        "ab44b514692f04baa4c1881df3814c1bf0789ac09e47922f60f5c8d18c2cc2b2",
        "https://www.pexels.com/video/aerial-view-of-people-enjoying-a-pool-28559383/",
        "Vitalis Nwenyi", PEXELS_LICENSE, "crop=iw*3/5:ih:iw*2/5:0,scale=768:720"),
}


def model_path(name: str, settings: Settings | None = None) -> Path:
    base = settings.models_dir if settings else Path("models")
    return base / MODEL_FILES[name].filename


def sample_path(name: str, settings: Settings | None = None) -> Path:
    base = settings.data_dir if settings else Path("data")
    return base / "samples" / name


ENGINES = {"auto": cv2.dnn.ENGINE_AUTO, "new": cv2.dnn.ENGINE_NEW, "classic": cv2.dnn.ENGINE_CLASSIC}


def embedder_path(name: str, file: str, settings: Settings | None = None) -> Path:
    base = settings.models_dir if settings else Path("models")
    return base / EMBEDDERS[name].folder / file


def load_net(name: str, settings: Settings | None = None, engine: str | None = None) -> cv2.dnn.Net:
    """A model by name on the settings' engine, or on `engine` when a caller knows better."""
    return read_net(model_path(name, settings), engine or (settings.dnn_engine if settings else "auto"))


def extra_path(file: str, settings: Settings | None = None) -> Path:
    base = settings.models_dir if settings else Path("models")
    return base / file


def read_net(path: Path, engine: str = "auto") -> cv2.dnn.Net:
    """Read an ONNX file on the chosen engine. The classic engine cannot read some graphs (PP-OCRv5
    recognition); those fall back to the new engine."""
    try:
        return cv2.dnn.readNetFromONNX(str(path), ENGINES[engine])
    except cv2.error:
        if engine != "classic" or not path.exists():
            raise
        log.info("%s: the classic engine cannot read it, using the new engine", path.name)
        return cv2.dnn.readNetFromONNX(str(path), cv2.dnn.ENGINE_NEW)


def vocabulary(name: str, settings: Settings | None = None) -> list[str]:
    """A text recogniser's symbols in class order, after the CTC blank at index 0."""
    if name in CHARSETS:
        return list(CHARSETS[name])
    import yaml
    side = next(iter(MODEL_EXTRAS[name]))
    config = yaml.safe_load(extra_path(side, settings).read_text(encoding="utf-8"))
    return [str(c) for c in config["PostProcess"]["character_dict"]] + [" "]  # PaddleOCR adds the space last
