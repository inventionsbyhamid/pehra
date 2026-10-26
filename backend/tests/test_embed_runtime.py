"""The image and text model's runtimes: ONNX Runtime (the default) gives OpenCV DNN's vectors in about half the
memory. In a module of its own, so the other embedder tests'
models are gone before these load theirs (the dev container's memory is shared)."""
import gc

import numpy as np

from test_embed import RED_TOP, crop
from watchful.config import Settings
from watchful.vision.embed import OnnxEmbedder as Embedder


def settings(**kw) -> Settings:
    return Settings(session_secret="y", models_dir=kw.pop("models_dir", "models"), _env_file=None, **kw)


def test_onnx_runtime_gives_the_vectors_opencv_gave():
    image, words = crop(RED_TOP), "a person wearing a red jacket"
    got = {}
    for runtime in ("onnxruntime", "opencv"):  # one at a time: each holds its towers
        e = Embedder("siglip2_b32", settings(embedder_runtime=runtime))
        assert e.runtime == runtime
        got[runtime] = (e.image(image), e.text(words))
        del e
        gc.collect()
    (a_image, a_text), (b_image, b_text) = got["onnxruntime"], got["opencv"]
    assert float(np.dot(a_image, b_image)) > 0.9999 and float(np.dot(a_text, b_text)) > 0.9999

