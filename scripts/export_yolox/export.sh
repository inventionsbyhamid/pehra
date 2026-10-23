#!/usr/bin/env bash
# Inside watchful-export2: raw head outputs (no --decode_in_inference), opset 12 with onnxsim (onnxsim 0.8.1 refuses opset 11; release files are opset 11 simplified).
set -euo pipefail
mkdir -p /work/out
cd /work/src/YOLOX
export PYTHONPATH=/work/src/YOLOX
for m in nano tiny; do
  for s in 320 384 416; do
    python /work/scripts/export.py -n yolox-$m -c /work/models/yolox_$m.pth \
      --output-name /work/out/yolox_${m}_${s}.onnx -o 12 test_size $s,$s
  done
done
ls -la /work/out
