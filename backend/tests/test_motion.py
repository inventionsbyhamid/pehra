import numpy as np

from watchful.vision.motion import MotionGate


def scene(x=None):
    img = np.zeros((360, 640, 3), np.uint8)
    img[300:, :] = 60
    if x is not None:
        img[100:180, x:x + 60] = 255
    return img


def test_static_scene_not_moving():
    gate = MotionGate()
    for _ in range(30):
        result = gate.update(scene())
    assert result.moving is False


def test_moving_square_detected():
    gate = MotionGate()
    for _ in range(30):
        gate.update(scene())
    for x in range(100, 300, 20):
        result = gate.update(scene(x))
    assert result.moving is True
    bx, by, bw, bh = max(result.boxes, key=lambda b: b[2] * b[3])
    assert bx < 280 + 60 and bx + bw > 280 and by < 180 and by + bh > 100


def test_ratio_bounds():
    gate = MotionGate()
    for x in range(0, 400, 40):
        r = gate.update(scene(x))
        assert 0.0 <= r.ratio <= 1.0
