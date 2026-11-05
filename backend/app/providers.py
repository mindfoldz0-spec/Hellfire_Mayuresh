"""Outbound AI provider calls: retries, circuit breaker, and live status.

Every external dependency (Sarvam ASR, AWS SER, Hugging Face Space, Groq) goes
through here so that a provider outage degrades the product predictably instead
of silently producing half a triage — and so the console can say *which* parts
of a dossier are real.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests

from backend.app.config import (
    AI_OFFLINE,
    PROVIDER_ATTEMPTS,
    PROVIDER_BACKOFF_SECONDS,
    PROVIDER_BREAKER_COOLDOWN,
    PROVIDER_BREAKER_THRESHOLD,
)

logger = logging.getLogger("cers.providers")


@dataclass
class ProviderState:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    open_until: float = 0.0
    last_error: str = ""
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return round(self.total_latency_ms / self.successes, 1) if self.successes else 0.0


class ProviderRegistry:
    """Thread-safe health tracking for each outbound dependency."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: Dict[str, ProviderState] = {}

    def _state(self, name: str) -> ProviderState:
        state = self._states.get(name)
        if state is None:
            state = ProviderState()
            self._states[name] = state
        return state

    def is_open(self, name: str) -> bool:
        with self._lock:
            return time.time() < self._state(name).open_until

    def record_success(self, name: str, latency_ms: float) -> None:
        with self._lock:
            state = self._state(name)
            state.calls += 1
            state.successes += 1
            state.consecutive_failures = 0
            state.total_latency_ms += latency_ms

    def record_failure(self, name: str, error: str) -> None:
        with self._lock:
            state = self._state(name)
            state.calls += 1
            state.failures += 1
            state.consecutive_failures += 1
            state.last_error = error[:200]
            if state.consecutive_failures >= PROVIDER_BREAKER_THRESHOLD:
                state.open_until = time.time() + PROVIDER_BREAKER_COOLDOWN
                logger.error(
                    "Circuit opened for %s after %d failures (cooldown %.0fs)",
                    name,
                    state.consecutive_failures,
                    PROVIDER_BREAKER_COOLDOWN,
                )

    def reset(self) -> None:
        with self._lock:
            self._states.clear()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            return {
                name: {
                    "calls": state.calls,
                    "successes": state.successes,
                    "failures": state.failures,
                    "breaker_open": now < state.open_until,
                    "avg_latency_ms": state.avg_latency_ms,
                    "last_error": state.last_error,
                }
                for name, state in self._states.items()
            }


registry = ProviderRegistry()


def request(
    provider: str,
    method: str,
    url: str,
    *,
    timeout: float,
    **kwargs: Any,
) -> Optional[requests.Response]:
    """Perform one HTTP call with retries and breaker protection.

    Returns the response for any non-5xx status (a 4xx is a *successful* call
    with a client error — retrying it would be wrong), or ``None`` when the
    provider is offline, suppressed by the breaker, or exhausted its retries.
    """
    if AI_OFFLINE:
        return None
    if registry.is_open(provider):
        return None

    attempts = max(1, PROVIDER_ATTEMPTS)
    last_error = "unknown error"
    for attempt in range(attempts):
        started = time.perf_counter()
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            latency_ms = (time.perf_counter() - started) * 1000.0
            if response.status_code >= 500:
                raise requests.HTTPError(
                    f"HTTP {response.status_code}", response=response
                )
            registry.record_success(provider, latency_ms)
            return response
        except Exception as exc:  # noqa: BLE001 - any transport/5xx failure
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < attempts:
                time.sleep(PROVIDER_BACKOFF_SECONDS * (2**attempt))

    registry.record_failure(provider, last_error)
    logger.warning("Provider %s unavailable: %s", provider, last_error)
    return None


def available(provider: str) -> bool:
    """False when an explicit offline mode or an open breaker suppresses calls."""
    return not AI_OFFLINE and not registry.is_open(provider)
