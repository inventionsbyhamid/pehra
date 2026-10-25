"""The pool that runs AI checks: a live camera's critical candidate goes before everything else, then other live
candidates, then an upload's, each kind in the order it came. An upload of a long video queues many checks; a
drowning on a live camera must not wait behind them."""
import heapq
import itertools
import threading
from concurrent.futures import Future


def live_first(fn, args: tuple, kwargs: dict) -> int:
    """The priority of a WorkerManager._verify_and_store(pack, incident_id=None, live=False) call: lower runs first."""
    live = args[2] if len(args) > 2 else kwargs.get("live", False)
    pack = args[0] if args else kwargs.get("pack")
    critical = getattr(getattr(pack, "candidate", None), "severity", None) == "critical"
    return 0 if live and critical else 1 if live else 2


class CheckPool:
    """A thread pool with ThreadPoolExecutor's submit and shutdown, whose queue is ordered by `priority`."""

    def __init__(self, workers: int, priority=live_first):
        self.priority = priority
        self._queue: list = []
        self._order = itertools.count()
        self._ready = threading.Condition()
        self._closed = False
        self._threads = [threading.Thread(target=self._run, daemon=True, name=f"checks-{i}")
                         for i in range(max(1, workers))]
        for thread in self._threads:
            thread.start()

    def submit(self, fn, *args, **kwargs) -> Future:
        future: Future = Future()
        with self._ready:
            if self._closed:
                raise RuntimeError("the check pool is shut down")
            heapq.heappush(self._queue, (self.priority(fn, args, kwargs), next(self._order), future, fn, args, kwargs))
            self._ready.notify()
        return future

    def _run(self) -> None:
        while True:
            with self._ready:
                while not self._queue and not self._closed:
                    self._ready.wait()
                if not self._queue:
                    return
                _, _, future, fn, args, kwargs = heapq.heappop(self._queue)
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as e:  # the caller reads it from the future, as with ThreadPoolExecutor
                future.set_exception(e)

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        with self._ready:
            self._closed = True
            if cancel_futures:
                for _, _, future, *_ in self._queue:
                    future.cancel()
                self._queue.clear()
            self._ready.notify_all()
        if wait:
            for thread in self._threads:
                thread.join()
