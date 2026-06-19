"""
engine/metrics.py — Prometheus metrics exposed by the orchestrator.

Exposes an HTTP server on port 9100 (scraped by Prometheus as job=closed-loop).
Import start_metrics_server() and call it once at orchestrator startup.

Metrics:
    closed_loop_actions_total{service, runbook, outcome}
        outcome: success | rollback | fail | dry_run

    closed_loop_circuit_breaker_state{service}
        0 = CLOSED (normal), 1 = OPEN (halted)

    closed_loop_blast_radius_remaining{service}
        Remaining actions allowed in the current blast-radius minute window

    closed_loop_mutex_locked{service}
        0 = FREE, 1 = LOCKED (runbook executing)

    closed_loop_verify_status{service, runbook}
        0 = fail, 1 = pass, 2 = in_progress
"""

from prometheus_client import Counter, Gauge, start_http_server

# ── Counters ──────────────────────────────────────────────────────────────────

action_counter = Counter(
    "closed_loop_actions_total",
    "Total closed-loop actions executed",
    ["service", "runbook", "outcome"],
)

# ── Gauges ────────────────────────────────────────────────────────────────────

circuit_breaker_gauge = Gauge(
    "closed_loop_circuit_breaker_state",
    "Circuit-breaker state per service (0=CLOSED 1=OPEN)",
    ["service"],
)

blast_radius_gauge = Gauge(
    "closed_loop_blast_radius_remaining",
    "Remaining global actions allowed in the current blast-radius minute window",
    ["service"],
)

mutex_gauge = Gauge(
    "closed_loop_mutex_locked",
    "Per-service mutex state (0=FREE 1=LOCKED)",
    ["service"],
)

verify_status_gauge = Gauge(
    "closed_loop_verify_status",
    "Last verify result per service+runbook (0=fail 1=pass 2=in_progress)",
    ["service", "runbook"],
)

# ── Server ────────────────────────────────────────────────────────────────────

_METRICS_PORT = 9100
_started = False


def start_metrics_server(port: int = _METRICS_PORT) -> None:
    """Start the Prometheus HTTP server. Idempotent — safe to call multiple times."""
    global _started
    if _started:
        return
    start_http_server(port)
    _started = True
    print(f"[metrics] Prometheus metrics server started on :{port}", flush=True)
