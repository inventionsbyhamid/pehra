import cv2

from watchful.vision.detect import YoloxDetector as Detector
from watchful.vision.models import sample_path
from watchful.vision.pose import MediaPipePose as PoseEstimator


def test_pose_on_vtest_person():
    cap = cv2.VideoCapture(str(sample_path("vtest.avi")))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 50)
    _, img = cap.read()
    people = [d for d in Detector().detect(img) if d.cls == "person"]
    box = max(people, key=lambda d: d.box[3]).box
    result = PoseEstimator().estimate(img, box)
    assert result is not None
    assert result.confidence > 0.3
    assert result.torso_angle_deg < 30
    x, y, w, h = box
    assert y - 0.2 * h < result.hip_y < y + h
