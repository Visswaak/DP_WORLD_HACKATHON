from __future__ import annotations

import logging
import threading
from collections import Counter
from contextlib import contextmanager
from time import perf_counter


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("customs_ai")


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self._timings_ms: Counter[str] = Counter()

    def incr(self, key: str, value: int = 1) -> None:
        with self._lock:
            self._counters[key] += value

    def timing(self, key: str, value_ms: float) -> None:
        with self._lock:
            self._timings_ms[key] += round(value_ms, 2)

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "timings_ms_total": dict(self._timings_ms),
            }


metrics = Metrics()


@contextmanager
def timed(metric_name: str):
    start = perf_counter()
    try:
        yield
    finally:
        metrics.timing(metric_name, (perf_counter() - start) * 1000)
