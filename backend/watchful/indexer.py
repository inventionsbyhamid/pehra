"""Fills in image embeddings for the search index in the background, so cameras and scans never wait for it."""
import logging
import threading
import time

import cv2
import numpy as np

from watchful.store.blobs import in_society

log = logging.getLogger("watchful.indexer")
INDEX_KINDS = ["scene", "track_end"]


def reset_if_model_changed(repo, model: str) -> bool:
    """Embeddings from different models can't be compared: a new model clears the index so it is rebuilt."""
    previous = repo.get_meta("embedder")
    if previous == model:
        return False
    log.info("embedding model is now %s (was %s); rebuilding the search index (%d moments)", model, previous,
             repo.clear_embeddings())
    repo.set_meta("embedder", model)
    return True


def index_once(repo, blobs, embedder, kinds: list[str] = INDEX_KINDS, limit: int = 64) -> int:
    """Embeds up to `limit` of one society's newest unindexed moments, reading only pictures under its own key
    prefix."""
    done = 0
    for e in repo.events_missing_embedding(kinds, limit):
        if not in_society(e.keyframe_key, repo.workspace_id):
            continue
        try:
            img = cv2.imdecode(np.frombuffer(blobs.get(e.keyframe_key), np.uint8), cv2.IMREAD_COLOR)
        except (OSError, KeyError):
            img = None
        if img is None:
            continue
        repo.set_event_embedding(e.id, embedder.image(img).tolist())
        done += 1
    return done


class Indexer:
    """`repos`: a repository, or a callable giving every society's (each society's index is its own)."""

    def __init__(self, repos, blobs, embedder_factory, every_s: float = 5.0, busy=lambda: False,
                 model: str | None = None):
        self.repos = repos if callable(repos) else (lambda: [repos])
        self.blobs, self.factory, self.every_s, self.model = blobs, embedder_factory, every_s, model
        self.busy = busy  # a scan is running: wait, so it gets the CPUs
        self._embedder = None
        self._checked: set[str] = set()  # societies whose index was checked against the model
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.every_s):
            if self.busy():
                continue
            try:
                if self._embedder is None:
                    self._embedder = self.factory()
                repos = list(self.repos())
                for repo in repos:
                    if self.model and repo.workspace_id not in self._checked:
                        reset_if_model_changed(repo, self.model)
                        self._checked.add(repo.workspace_id)
                # a batch per society in turn, so a busy society never keeps another's search behind
                busy = repos
                while busy and not self.busy() and not self._stop.is_set():
                    busy = [r for r in busy if index_once(r, self.blobs, self._embedder)]
            except Exception:
                log.exception("indexing failed")
                time.sleep(30)
