# YOLOX at smaller input sizes

`yolox_nano_320.onnx` and `yolox_nano_384.onnx` are exported from the official YOLOX nano weights (release
0.1.1rc0, Apache 2.0) with YOLOX's own `tools/export_onnx.py`, unchanged: raw head outputs, opset 12, onnxsim.
`export.py` only restores the `torch.onnx._export` alias that torch 2.5 removed.

Environment used: python 3.12, torch 2.5.1 (CPU), onnx 1.23.2, onnxsim 0.8.1, YOLOX at 6ddff48, weights
`yolox_nano.pth` from the release. Then, inside the YOLOX checkout with `PYTHONPATH` pointing at it:

```bash
python export.py -n yolox-nano -c yolox_nano.pth --output-name yolox_nano_320.onnx -o 12 test_size 320,320
```

On our 768x576 test clip against yolox_s as reference: nano 416 finds 97.5% of people in 102 ms per frame, nano 320
finds 95.3% in 56 ms (single thread, Xeon E5-2680 v2, classic engine). Scans use 320 by default; live cameras 416.
