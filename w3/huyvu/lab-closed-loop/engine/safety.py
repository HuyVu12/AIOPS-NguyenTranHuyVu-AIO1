"""
engine/safety.py — Blast-radius guard and circuit breaker.

BlastRadiusGuard:
    - max_actions_per_minute  : global sliding-window cap (all services)
    - max_restarts_per_hour   : per-service sliding-window cap

CircuitBreaker:
    - Opens after N consecutive verify/action failures
    - Manual reset only (restart the orchestrator process)
"""

import time
from collections import defaultdict, deque

from engine.logger import JsonLogger

log = JsonLogger("safety")


class BlastRadiusGuard:
    """Enforce per-minute global and per-service-per-hour action limits."""

    def __init__(self, max_per_minute: int, max_restarts_per_hour: int) -> None:
        self._max_per_minute = max_per_minute
        self._max_restarts_per_hour = max_restarts_per_hour
        self._global_window: deque[float] = deque()
        self._service_window: dict[str, deque[float]] = defaultdict(deque)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _prune(self, window: deque, horizon: float) -> None:
        while window and window[0] < horizon:
            window.popleft()

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self, service: str) -> tuple[bool, str]:
        """Return (allowed, reason). Does NOT record an action."""
        now = time.time()
        self._prune(self._global_window, now - 60)
        self._prune(self._service_window[service], now - 3600)

        if len(self._global_window) >= self._max_per_minute:
            return False, (
                f"global actions/min limit ({self._max_per_minute}) reached"
            )
        if len(self._service_window[service]) >= self._max_restarts_per_hour:
            return False, (
                f"restarts/hour limit ({self._max_restarts_per_hour}) for {service}"
            )
        return True, "ok"

    def record(self, service: str) -> None:
        """Record that an action was taken for *service* right now."""
        now = time.time()
        self._global_window.append(now)
        self._service_window[service].append(now)

    def remaining_global(self) -> int:
        now = time.time()
        self._prune(self._global_window, now - 60)
        return max(0, self._max_per_minute - len(self._global_window))


class CircuitBreaker:
    """
    Halt automation after N consecutive verify/action failures.
    Reset mode: manual (operator must restart the orchestrator).
    """

    def __init__(self, threshold: int) -> None:
        self._threshold = threshold
        self._failures = 0
        self._open = False

    def is_open(self) -> bool:
        return self._open

    def failure_count(self) -> int:
        return self._failures

    def record_failure(self) -> None:
        self._failures += 1
        log.warning(
            "CIRCUIT_BREAKER_FAILURE",
            consecutive_failures=self._failures,
            threshold=self._threshold,
        )
        if self._failures >= self._threshold:
            self._open = True
            log.error(
                "CIRCUIT_BREAKER_HALT",
                consecutive_failures=self._failures,
                threshold=self._threshold,
                message="Automation halted. Manual intervention required.",
            )

    def record_success(self) -> None:
        self._failures = 0
