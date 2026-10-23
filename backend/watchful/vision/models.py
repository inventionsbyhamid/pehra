from dataclasses import dataclass
from pathlib import Path

import cv2

from watchful.config import Settings

HF = "https://huggingface.co/opencv"


@dataclass(frozen=True)
class ModelFile:
    url: str
    filename: str
    license: str
    sha256: str | None = None


MODEL_FILES: dict[str, ModelFile] = {
    "yolox_tiny": ModelFile(
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
        "yolox_tiny.onnx", "Apache-2.0 (Megvii YOLOX)",
        "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"),
    "yolox_s": ModelFile(
        f"{HF}/object_detection_yolox/resolve/main/object_detection_yolox_2022nov.onnx",
        "object_detection_yolox_2022nov.onnx", "Apache-2.0 (OpenCV Model Zoo)",
        "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"),
    "person": ModelFile(
        f"{HF}/person_detection_mediapipe/resolve/main/person_detection_mediapipe_2023mar.onnx",
        "person_detection_mediapipe_2023mar.onnx", "Apache-2.0 (OpenCV Model Zoo, MediaPipe)",
        "47fd5599d6fa17608f03e0eb0ae230baa6e597d7e8a2c8199fe00abea55a701f"),
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

SAMPLES = {
    "vtest.avi": "https://raw.githubusercontent.com/opencv/opencv/5.x/samples/data/vtest.avi",
}


def model_path(name: str, settings: Settings | None = None) -> Path:
    base = settings.models_dir if settings else Path("models")
    return base / MODEL_FILES[name].filename


def sample_path(name: str, settings: Settings | None = None) -> Path:
    base = settings.data_dir if settings else Path("data")
    return base / "samples" / name


def load_net(name: str, settings: Settings | None = None) -> cv2.dnn.Net:
    return cv2.dnn.readNetFromONNX(str(model_path(name, settings)), cv2.dnn.ENGINE_AUTO)
