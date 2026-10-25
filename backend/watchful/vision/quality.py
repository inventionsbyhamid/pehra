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


def grey(image: np.ndarray) -> np.ndarray:
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def order_quad(points) -> np.ndarray:
    """Four corners as top left, top right, bottom right, bottom left (clockwise on screen)."""
    q = np.asarray(points, np.float32).reshape(-1, 2)
    c = q.mean(0)
    q = q[np.argsort(np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0]))]
    return np.roll(q, -int(np.argmin(q.sum(1))), axis=0)


def widen(quad: np.ndarray, margin: float) -> np.ndarray:
    """The quad grown about its centre by `margin` of its size (0.1 adds 5% on each side)."""
    c = quad.mean(0)
    return (c + (quad - c) * (1 + margin)).astype(np.float32)


def flatten(image: np.ndarray, quad: np.ndarray, scale: float = 1.0,
            interpolation: int = cv2.INTER_LINEAR) -> tuple[np.ndarray, np.ndarray]:
    """The quad warped to an upright rectangle `scale` times its size, and the homography from image to crop."""
    tl, tr, br, bl = quad
    w = max(4, round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)) * scale))
    h = max(4, round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)) * scale))
    m = cv2.getPerspectiveTransform(quad.astype(np.float32),
                                    np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]))
    return cv2.warpPerspective(image, m, (w, h), flags=interpolation, borderMode=cv2.BORDER_REPLICATE), m
