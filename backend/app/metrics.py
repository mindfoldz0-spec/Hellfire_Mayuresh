"""In-process dispatch metrics.

Deliberately dependency-free (no imports from other app modules) so that any
module can record a datapoint without creating an import cycle. Rendered by
``GET /api/metrics`` and the officer console's METRICS panel.
"""

from __future__ import annotations

import math
import statistics
import threading
import time
from collections import Counter
from typing import Any, Dict, List

BAND_CRITICAL = "CRITICAL"
BAND_HIGH = "HIGH"
BAND_OTHER = "OTHER"


def band_for(priority: int) -> str:
    if priority >= 85:
        return BAND_CRITICAL
    if priority >= 65:
        return BAND_HIGH
    return BAND_OTHER


def _percentiles(values: List[float]) -> Dict[str, Any]:
    """Nearest-rank median and p90 for a bucket of claim latencies."""
    count = len(values)
    if count == 0:
        return {"samples": 0, "median_s": None, "p90_s": None}
    ordered = sorted(values)
    if count == 1:
        sample = round(ordered[0], 1)
        return {"samples": 1, "median_s": sample, "p90_s": sample}

    def nearest_rank(fraction: float) -> float:
        rank = max(1, math.ceil(fraction * count))
        return round(ordered[rank - 1], 1)

    return {
        "samples": count,
        "median_s": round(statistics.median(ordered), 1),
        "p90_s": nearest_rank(0.9),
    }


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.started_at = time.time()
            self.counters: Counter[str] = Counter()
            self.categories: Counter[str] = Counter()
            self._time_to_claim: Dict[str, List[float]] = {
                BAND_CRITICAL: [],
                BAND_HIGH: [],
                BAND_OTHER: [],
            }

    # -- recording ---------------------------------------------------------
    def incr(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] += amount

    def observe_claim(self, priority: int, seconds: float) -> None:
        with self._lock:
            self.counters["calls_claimed"] += 1
            bucket = self._time_to_claim[band_for(int(priority or 0))]
            bucket.append(max(0.0, seconds))
            if len(bucket) > 500:
                del bucket[:-500]

    def observe_category(self, category: str) -> None:
        if not category:
            return
        with self._lock:
            self.categories[category] += 1

    # -- reporting ---------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            claim_stats = {
                band: _percentiles(values)
                for band, values in self._time_to_claim.items()
            }
            counters = dict(self.counters)
            categories = dict(self.categories.most_common(10))
            uptime = round(time.time() - self.started_at, 1)
        triage_ok = counters.get("triage_ok", 0)
        heuristic = counters.get("triage_heuristic", 0)
        total = triage_ok + heuristic
        return {
            "uptime_seconds": uptime,
            "counters": counters,
            "time_to_claim": claim_stats,
            "categories": categories,
            "heuristic_fallback_rate": round(heuristic / total, 3) if total else 0.0,
        }


metrics = Metrics()
