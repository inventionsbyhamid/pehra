import cv2
import numpy as np

DARK_V = 60
_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def sharpness(image: np.ndarray) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def is_dark(image: np.ndarray) -> bool:
    return float(cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[..., 2].mean()) < DARK_V


def brighten(image: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lab[..., 0] = _clahe.apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def best_crop(crops: list[tuple[np.ndarray, float]]) -> int:
    """Index of the crop with the best sharpness times box area."""
    return max(range(len(crops)), key=lambda i: sharpness(crops[i][0]) * crops[i][1])
