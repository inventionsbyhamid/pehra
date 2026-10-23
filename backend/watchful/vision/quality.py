import cv2
import numpy as np

from watchful.tuning import QualityTuning


def sharpness(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def is_dark(image: np.ndarray, cfg: QualityTuning = QualityTuning()) -> bool:
    return float(cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[..., 2].mean()) < cfg.dark_v


def brighten(image: np.ndarray, cfg: QualityTuning = QualityTuning()) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=cfg.clahe_clip, tileGridSize=(cfg.clahe_tiles, cfg.clahe_tiles))
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lab[..., 0] = clahe.apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def best_crop(crops: list[tuple[np.ndarray, float]]) -> int:
    """Index of the crop with the best sharpness times box area."""
    return max(range(len(crops)), key=lambda i: sharpness(crops[i][0]) * crops[i][1])
