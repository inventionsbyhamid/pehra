from dataclasses import dataclass
from pathlib import Path

import cv2

from watchful.config import Settings

HF = "https://huggingface.co/opencv"
OUR_RELEASE = "https://github.com/inventionsbyhamid/watchful/releases/download/models-v1"


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
    # exported from the same nano weights at smaller inputs (scripts/export_yolox), served from our release
    "yolox_nano_320": ModelFile(
        f"{OUR_RELEASE}/yolox_nano_320.onnx", "yolox_nano_320.onnx", "Apache-2.0 (Megvii YOLOX, exported)",
        "feb9d0f814e1ad06117711fc0b563ff708f5a893c58b7c3c14a7ed102b7d546d"),
    "yolox_nano_384": ModelFile(
        f"{OUR_RELEASE}/yolox_nano_384.onnx", "yolox_nano_384.onnx", "Apache-2.0 (Megvii YOLOX, exported)",
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
}

SIGLIP2 = "https://huggingface.co/onnx-community/siglip2-base-patch16-224-ONNX/resolve/main/"


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


EMBEDDERS: dict[str, EmbedderSpec] = {
    "siglip2": EmbedderSpec(
        "siglip2",
        {"vision.onnx": SIGLIP2 + "onnx/vision_model.onnx", "text.onnx": SIGLIP2 + "onnx/text_model.onnx",
         "tokenizer.json": SIGLIP2 + "tokenizer.json"},
        "Apache-2.0 (Google SigLIP 2, ONNX by onnx-community)",
        224, 0.5, 0.5, 64, 0, True, "pooler_output"),
}

SAMPLE_BASE = "https://raw.githubusercontent.com/opencv/opencv/5.x/samples/data/"
SAMPLES = {name: SAMPLE_BASE + name for name in ("vtest.avi", "basketball1.png", "basketball2.png", "messi5.jpg")}


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


def load_net(name: str, settings: Settings | None = None) -> cv2.dnn.Net:
    engine = ENGINES[settings.dnn_engine if settings else "auto"]
    return cv2.dnn.readNetFromONNX(str(model_path(name, settings)), engine)
