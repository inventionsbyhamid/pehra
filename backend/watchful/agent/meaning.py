"""The meaning check behind the age guard. What a watch compares pictures with (looks_like and zone_looks_like
texts, a described subject) is embedded with the image model's text tower, which is multilingual, and refused when
it sits nearer its nearest child anchor than its nearest ordinary anchor by Tuning signals.child_meaning_margin
(anchors in data/compile.json). Word lists miss paraphrases, other spellings and other scripts; this catches many of
them. It runs when a watch is compiled or saved, never per frame."""
import json
import threading
import unicodedata
from functools import lru_cache

import numpy as np

from watchful.rules.vocab import DATA


@lru_cache
def anchors() -> tuple[tuple[str, ...], tuple[str, ...]]:
    words = json.loads((DATA / "compile.json").read_text())
    return tuple(words["child_anchors"]), tuple(words["ordinary_anchors"])


class ChildMeaning:
    """Nearest child anchor against nearest ordinary anchor, by cosine in the text tower's space. A plain cosine to
    the child anchors alone does not separate: short everyday texts ("a person", "smoke") sit as close to "a kid"
    as most ages do."""

    def __init__(self, embedder, margin: float, child=None, ordinary=None, keep: int = 4096):
        default_child, default_ordinary = anchors()
        self.embedder, self.margin, self.keep = embedder, margin, keep
        self.child, self.ordinary = list(child or default_child), list(ordinary or default_ordinary)
        self._vectors: tuple[np.ndarray, np.ndarray] | None = None
        self._seen: dict[str, tuple[float, str, str]] = {}
        self._lock = threading.Lock()

    def _anchor_vectors(self) -> tuple[np.ndarray, np.ndarray]:
        with self._lock:
            if self._vectors is None:
                self._vectors = (np.stack([self.embedder.text(t) for t in self.child]),
                                 np.stack([self.embedder.text(t) for t in self.ordinary]))
            return self._vectors

    def score(self, text: str) -> tuple[float, str, str]:
        """How much nearer the text is to its nearest child anchor than to its nearest ordinary anchor, and the two
        anchors."""
        text = unicodedata.normalize("NFC", str(text)).strip()
        with self._lock:
            if text in self._seen:
                return self._seen[text]
        child, ordinary = self._anchor_vectors()
        v = np.asarray(self.embedder.text(text), np.float32)
        c, o = child @ v, ordinary @ v
        out = (float(c.max() - o.max()), self.child[int(c.argmax())], self.ordinary[int(o.argmax())])
        with self._lock:
            if len(self._seen) >= self.keep:
                self._seen.clear()
            self._seen[text] = out
        return out

    def __call__(self, text) -> bool:
        """True when the text means a child: it is refused."""
        return bool(str(text or "").strip()) and self.score(text)[0] >= self.margin


_made: dict[tuple, ChildMeaning] = {}
_made_lock = threading.Lock()


def child_meaning(embedder, margin: float) -> ChildMeaning:
    """One check per embedder and margin, so the anchors are embedded once."""
    with _made_lock:
        key = (id(embedder), margin)
        if key not in _made:
            _made[key] = ChildMeaning(embedder, margin)
        return _made[key]


def default_meaning() -> ChildMeaning:
    """The process's check, with the embedder and Tuning the app runs with (EMBEDDER, MODELS_DIR, TUNING_FILE). The
    text tower loads on first use when it is not loaded already."""
    from watchful.components import make_embedder
    from watchful.config import get_settings
    from watchful.tuning import load_tuning
    settings = get_settings()
    return child_meaning(make_embedder(settings), load_tuning(settings.tuning_file).signals.child_meaning_margin)
