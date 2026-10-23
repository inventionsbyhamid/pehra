"""Fills in image embeddings for the search index in the background, so cameras and scans never wait for it."""
import logging
import threading
import time

import cv2
import numpy as np

log = logging.getLogger("watchful.indexer")
INDEX_KINDS = ["scene", "track_end"]


def index_once(repo, blobs, embedder, kinds: list[str] = INDEX_KINDS, limit: int = 64) -> int:
    done = 0
    for e in repo.events_missing_embedding(kinds, limit):
        if not e.keyframe_key:
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
    def __init__(self, repo, blobs, embedder_factory, every_s: float = 5.0, busy=lambda: False):
        self.repo, self.blobs, self.factory, self.every_s = repo, blobs, embedder_factory, every_s
        self.busy = busy  # a scan is running: wait, so it gets the CPUs
        self._embedder = None
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
                while not self.busy() and index_once(self.repo, self.blobs, self._embedder) and not self._stop.is_set():
                    pass
            except Exception:
                log.exception("indexing failed")
                time.sleep(30)
