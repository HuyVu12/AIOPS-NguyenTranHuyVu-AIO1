#!/usr/bin/env python3
"""
closed_loop.py — Ronki closed-loop auto-remediation orchestrator.

Usage:
    uv run python closed_loop.py --config config.yaml [--dry-run]

Implements all 5 mandatory sub-checkpoints:
    1. Dry-run          — every runbook is dry-run validated before execution
    2. Blast-radius     — per-minute global + per-service-per-hour caps
    3. Verify post-act  — Prometheus polling ≥ 3 consecutive passes in 60s
    4. Auto-rollback    — verify fail → rollback runbook executed automatically
    5. Circuit breaker  — 3 consecutive failures → HALT (manual reset)

Stress extensions (excellent level):
    S1. Transactional multi-step rollback  — completed steps rolled back in LIFO order
    S2. Per-service mutex                  — concurrent alerts on same service → SERVICE_LOCK_BUSY
    S3. Decision validation / LLM defense  — runbook not in registry → DECISION_VALIDATION_FAILED
"""

import argparse
import json
import subprocess
import threading
import time
from pathlib import Path

import requests
import yaml

from engine.logger import JsonLogger
from engine.metrics import (
    action_counter,
    blast_radius_gauge,
    circuit_breaker_gauge,
    mutex_gauge,
    start_metrics_server,
    verify_status_gauge,
)
from engine.safety import BlastRadiusGuard, CircuitBreaker
from engine.verify import verify_service

log = JsonLogger("orchestrator")

# ── Per-service mutex registry ────────────────────────────────────────────────
# Keyed by service name. Different services have independent locks so they
# always run in parallel; the same service serializes (blocking=False → skip).

_service_locks: dict[str, threading.Lock] = {}
_locks_meta = threading.Lock()


def get_service_lock(service: str) -> threading.Lock:
    with _locks_meta:
        if service not in _service_locks:
            _service_locks[service] = threading.Lock()
        return _service_locks[service]


# ── Config loading ────────────────────────────────────────────────────────────

def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Alertmanager polling ──────────────────────────────────────────────────────

def fetch_active_alerts(alertmanager_url: str) -> list[dict]:
    """Return active, non-silenced, non-inhibited alerts from Alertmanager."""
    try:
        resp = requests.get(
            f"{alertmanager_url}/api/v2/alerts",
            params={"active": "true", "silenced": "false", "inhibited": "false"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.error("ALERTMANAGER_FETCH_ERROR", error=str(exc))
        return []


# ── Runbook execution ─────────────────────────────────────────────────────────

def run_runbook(
    script: str,
    service: str,
    dry_run: bool,
    timeout_s: int = 30,
    extra_args: list[str] | None = None,
) -> bool:
    """
    Execute a runbook script via bash.

    Parameters
    ----------
    script     : path to .sh file (relative to CWD or absolute)
    service    : service name, passed as --service <name>
    dry_run    : if True, passes --dry-run flag to the script
    timeout_s  : max seconds before SIGKILL
    extra_args : additional CLI arguments forwarded verbatim

    Returns
    -------
    True  → exit code 0
    False → non-zero or timeout
    """
    cmd = ["bash", script, "--service", service]
    if dry_run:
        cmd.append("--dry-run")
    if extra_args:
        cmd.extend(extra_args)

    log.info("RUNBOOK_EXEC", script=script, service=service, dry_run=dry_run,
             extra_args=extra_args or [])
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s
        )
        log.info(
            "RUNBOOK_RESULT",
            script=script,
            service=service,
            dry_run=dry_run,
            returncode=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        log.error("RUNBOOK_TIMEOUT", script=script, service=service,
                  timeout_s=timeout_s)
        return False
    except Exception as exc:
        log.error("RUNBOOK_ERROR", script=script, service=service, error=str(exc))
        return False


# ── Decision helpers ──────────────────────────────────────────────────────────

def extract_service(alert: dict) -> str:
    """Extract service name from alert labels."""
    labels = alert.get("labels", {})
    service = labels.get("service") or labels.get("job")
    if not service:
        log.warning(
            "EXTRACT_SERVICE_FALLBACK",
            labels=labels,
            message="No service or job label found, falling back to 'unknown'",
        )
        return "unknown"
    return service


def validate_runbook(
    runbook: str,
    cfg: dict,
    alertname: str,
    raw_decision: str,
) -> bool:
    """
    Stress S3: reject runbook paths not present in runbook_registry.

    The runbook_registry is an explicit whitelist of allowed script paths.
    If absent from config, it defaults to the values of runbook_map.
    A runbook not in the registry is treated as a hallucination or misconfiguration.
    """
    registry: list[str] = cfg.get(
        "runbook_registry",
        list(cfg.get("runbook_map", {}).values()),
    )
    if runbook in registry:
        return True
    log.error(
        "DECISION_VALIDATION_FAILED",
        bad_runbook=runbook,
        alertname=alertname,
        raw_decision=raw_decision,
        action="escalate_no_auto_action",
    )
    return False


# ── Transactional multi-step execution (Stress S1) ───────────────────────────

def run_transactional_steps(
    steps: list[str],
    service: str,
    dry_run: bool,
    timeout_s: int,
) -> tuple[bool, list[str]]:
    """
    Execute steps in order (A → B → C).

    Returns
    -------
    (True,  completed_steps)  → all steps succeeded
    (False, completed_steps)  → one step failed; completed_steps = steps that ran OK
    """
    completed: list[str] = []
    for step_script, step_args in steps:
        ok = run_runbook(step_script, service, dry_run=dry_run,
                         timeout_s=timeout_s, extra_args=step_args)
        if not ok:
            log.error(
                "TRANSACTIONAL_STEP_FAIL",
                step=step_script,
                service=service,
                completed_before_failure=completed,
            )
            return False, completed
        log.info("TRANSACTIONAL_STEP_COMPLETE", step=step_script, service=service)
        completed.append(step_script)
    return True, completed


# ── Core alert processing (with per-service lock) ─────────────────────────────

def process_alert(
    alert: dict,
    cfg: dict,
    baseline: dict,
    guard: BlastRadiusGuard,
    cb: CircuitBreaker,
    global_dry_run: bool,
) -> None:
    alertname = alert.get("labels", {}).get("alertname", "")
    service = extract_service(alert)

    log.info(
        "ALERT_DETECTED",
        alertname=alertname,
        service=service,
        severity=alert.get("labels", {}).get("severity", ""),
        fingerprint=alert.get("fingerprint", ""),
    )

    # ── 1. Decide: map alertname → runbook ───────────────────────────────────
    runbook = cfg["runbook_map"].get(alertname)
    if not runbook:
        log.warning("NO_RUNBOOK_MAPPING", alertname=alertname, service=service)
        return

    # S3: Decision validation — reject names absent from registry
    if not validate_runbook(runbook, cfg, alertname, raw_decision=runbook):
        return  # DECISION_VALIDATION_FAILED already logged; no subprocess spawned

    log.info("DECIDE_RUNBOOK", alertname=alertname, service=service, runbook=runbook)

    # ── 2. Blast-radius check ────────────────────────────────────────────────
    ok, reason = guard.check(service)
    if not ok:
        log.warning("BLAST_RADIUS_EXCEEDED", service=service, reason=reason)
        return
    log.info("BLAST_RADIUS_OK", service=service,
             remaining_global=guard.remaining_global())

    # ── S2: Per-service mutex — serialize actions on same service ─────────────
    svc_lock = get_service_lock(service)
    acquired = svc_lock.acquire(blocking=False)
    if not acquired:
        log.warning(
            "SERVICE_LOCK_BUSY",
            service=service,
            alertname=alertname,
            message="Another runbook is already executing for this service; skipping duplicate alert",
        )
        return

    mutex_gauge.labels(service=service).set(1)
    try:
        _process_alert_locked(
            alert, alertname, service, runbook, cfg, baseline, guard, cb, global_dry_run
        )
    finally:
        mutex_gauge.labels(service=service).set(0)
        svc_lock.release()


def _process_alert_locked(
    alert: dict,
    alertname: str,
    service: str,
    runbook: str,
    cfg: dict,
    baseline: dict,
    guard: BlastRadiusGuard,
    cb: CircuitBreaker,
    global_dry_run: bool,
) -> None:
    timeout_s: int = cfg["runbook_timeout_seconds"]

    # ── 3a. Dry-run (mandatory, even if global --dry-run is set) ─────────────
    if not run_runbook(runbook, service, dry_run=True, timeout_s=timeout_s):
        log.error("DRY_RUN_FAIL", runbook=runbook, service=service,
                  message="Dry-run returned non-zero; aborting")
        return
    log.info("DRY_RUN_PASS", runbook=runbook, service=service)

    # Short-circuit when global --dry-run is active
    if global_dry_run:
        action_counter.labels(service=service, runbook=runbook, outcome="dry_run").inc()
        log.info("GLOBAL_DRY_RUN_SKIP",
                 message="--dry-run flag set; skipping real action execution")
        return

    # ── 3b. Record blast-radius usage ─────────────────────────────────────────
    guard.record(service)
    blast_radius_gauge.labels(service=service).set(guard.remaining_global())

    # ── 3c. Execute action ────────────────────────────────────────────────────
    # S1: Check if this alert triggers a multi-step transactional deploy
    multi_step_map: dict[str, list] = cfg.get("multi_step_map", {})
    multi_step_rollback_map: dict[str, list] = cfg.get("multi_step_rollback_map", {})

    if alertname in multi_step_map:
        # Transactional multi-step execution (Stress S1)
        steps = multi_step_map[alertname]  # list of [script, [args]]
        steps_tuples = [(s[0], s[1] if len(s) > 1 else []) for s in steps]

        success, completed = run_transactional_steps(
            steps_tuples, service, dry_run=False, timeout_s=timeout_s
        )
        if not success:
            # Rollback completed steps in reverse order (LIFO)
            rollback_steps = multi_step_rollback_map.get(alertname, [])
            rollback_tuples = [(s[0], s[1] if len(s) > 1 else []) for s in rollback_steps]
            # Only rollback the steps that succeeded (len(completed))
            rollback_eligible = rollback_tuples[: len(completed)]
            rolled_back = []
            for rb_script, rb_args in reversed(rollback_eligible):
                log.warning("TRANSACTIONAL_ROLLBACK_STEP", step=rb_script, service=service)
                run_runbook(rb_script, service, dry_run=False,
                            timeout_s=timeout_s, extra_args=rb_args)
                rolled_back.append(rb_script)
            log.info(
                "TRANSACTIONAL_ROLLBACK_COMPLETE",
                service=service,
                rolled_back=rolled_back,
            )
            cb.record_failure()
            circuit_breaker_gauge.labels(service=service).set(1 if cb.is_open() else 0)
            return  # Do NOT log ACTION_SUCCESS for a failed transactional deploy
    else:
        # Standard single-step action
        if not run_runbook(runbook, service, dry_run=False, timeout_s=timeout_s):
            log.error("ACTION_EXEC_FAIL", runbook=runbook, service=service)
            cb.record_failure()
            circuit_breaker_gauge.labels(service=service).set(1 if cb.is_open() else 0)
            return

    log.info("ACTION_EXECUTED", runbook=runbook, service=service)

    # ── 4. Verify post-action ─────────────────────────────────────────────────
    t = baseline["verify_thresholds"]
    verify_status_gauge.labels(service=service, runbook=runbook).set(2)  # in_progress

    verify_ok = verify_service(
        prometheus_url=cfg["prometheus_url"],
        service=service,
        baseline=baseline,
        timeout_s=t["verify_timeout_seconds"],
        poll_interval_s=t["verify_poll_interval_seconds"],
        min_samples=t["verify_min_samples"],
    )

    if verify_ok:
        verify_status_gauge.labels(service=service, runbook=runbook).set(1)  # pass
        action_counter.labels(service=service, runbook=runbook, outcome="success").inc()
        log.info("ACTION_SUCCESS", alertname=alertname, service=service, runbook=runbook)
        cb.record_success()
        circuit_breaker_gauge.labels(service=service).set(0)
        return

    # ── 5. Auto-rollback on verify failure ────────────────────────────────────
    verify_status_gauge.labels(service=service, runbook=runbook).set(0)  # fail
    action_counter.labels(service=service, runbook=runbook, outcome="rollback").inc()

    rollback = cfg.get("rollback_map", {}).get(alertname, runbook)
    log.warning("ROLLBACK_TRIGGERED", service=service, rollback_runbook=rollback,
                alertname=alertname)
    rollback_ok = run_runbook(rollback, service, dry_run=False, timeout_s=timeout_s)
    log.info("ROLLBACK_EXECUTED", service=service, rollback_runbook=rollback,
             rollback_success=rollback_ok)

    cb.record_failure()
    circuit_breaker_gauge.labels(service=service).set(1 if cb.is_open() else 0)


# ── Main polling loop ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ronki closed-loop auto-remediation orchestrator"
    )
    parser.add_argument("--config", default="config.yaml",
                        help="Path to orchestrator config YAML")
    parser.add_argument("--dry-run", action="store_true",
                        help="Detect + decide + dry-run only; never execute real actions")
    args = parser.parse_args()

    cfg = load_config(args.config)

    # Resolve baseline.json relative to the config file's directory
    baseline_path = Path(args.config).parent / cfg["baseline_path"]
    with open(baseline_path, encoding="utf-8") as f:
        baseline = json.load(f)

    guard = BlastRadiusGuard(
        max_per_minute=cfg["blast_radius"]["max_actions_per_minute"],
        max_restarts_per_hour=cfg["blast_radius"]["max_restarts_per_service_per_hour"],
    )
    cb = CircuitBreaker(
        threshold=cfg["circuit_breaker"]["consecutive_failure_threshold"]
    )

    # Fingerprints seen in this run — prevents re-processing the same alert twice.
    # Cleared every 500 entries to avoid unbounded growth.
    seen: set[str] = set()

    start_metrics_server()

    log.info(
        "ORCHESTRATOR_START",
        config=args.config,
        dry_run=args.dry_run,
        poll_interval_s=cfg["poll_interval_seconds"],
        blast_radius=cfg["blast_radius"],
        circuit_breaker=cfg["circuit_breaker"],
    )

    poll_interval = cfg["poll_interval_seconds"]

    while True:
        # ── Circuit breaker gate ──────────────────────────────────────────────
        if cb.is_open():
            log.error("CIRCUIT_BREAKER_HALT",
                      message="Circuit OPEN — polling suspended. Manual reset required.")
            time.sleep(poll_interval)
            continue

        # ── Fetch and process active alerts ───────────────────────────────────
        alerts = fetch_active_alerts(cfg["alertmanager_url"])
        active_fps = {alert.get("fingerprint", "") for alert in alerts if alert.get("fingerprint")}
        seen = seen.intersection(active_fps)

        new_alerts = []
        for alert in alerts:
            fp = alert.get("fingerprint", "")
            if fp and fp in seen:
                continue
            if fp:
                seen.add(fp)
            new_alerts.append(alert)

        # Process each new alert in its own thread so different services run
        # in parallel (per-service mutex handles same-service serialization).
        threads = []
        for alert in new_alerts:
            t = threading.Thread(
                target=process_alert,
                args=(alert, cfg, baseline, guard, cb, args.dry_run),
                daemon=True,
            )
            t.start()
            threads.append(t)

        # Wait for all alert-processing threads before next poll cycle
        for t in threads:
            t.join()

        # Bounded seen-set to avoid infinite growth in long runs
        if len(seen) > 500:
            seen.clear()

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
