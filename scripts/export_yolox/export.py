# Runs the official YOLOX tools/export_onnx.py unchanged.
# torch 2.5 no longer has torch.onnx._export, which was an alias of torch.onnx.export.
import runpy
import sys

import torch

torch.onnx._export = torch.onnx.export
sys.argv = ["export_onnx.py"] + sys.argv[1:]
runpy.run_path("/work/src/YOLOX/tools/export_onnx.py", run_name="__main__")
