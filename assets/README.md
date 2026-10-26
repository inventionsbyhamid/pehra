# Models and sample footage

`scripts/fetch_assets.py` downloads these into `models/` and `data/samples/` and checks their sha256.

| Name | Source | License |
| --- | --- | --- |
| yolox_nano | YOLOX release 0.1.1rc0, `yolox_nano.onnx` (github.com/Megvii-BaseDetection/YOLOX) | Apache 2.0 |
| yolox_nano_320, yolox_nano_384 | Exported from the YOLOX nano weights at smaller inputs (`scripts/export_yolox`), kept here in `assets/yolox/` | Apache 2.0 |
| yolox_tiny | YOLOX release 0.1.1rc0, `yolox_tiny.onnx` (github.com/Megvii-BaseDetection/YOLOX) | Apache 2.0 |
| yolox_s | OpenCV Model Zoo, object_detection_yolox 2022nov | Apache 2.0 |
| pose | OpenCV Model Zoo, pose_estimation_mediapipe 2023mar | Apache 2.0 |
| yunet | OpenCV Model Zoo, face_detection_yunet 2023mar | MIT |
| sface | OpenCV Model Zoo, face_recognition_sface 2021dec | Apache 2.0 |
| vtest.avi | OpenCV samples (opencv/opencv 5.x, samples/data) | Apache 2.0 |
