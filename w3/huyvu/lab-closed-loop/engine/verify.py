"""
engine/verify.py — Prometheus-based post-action verification.

verify_service() polls Prometheus for up to `timeout_s` seconds.
It requires `min_samples` consecutive healthy reads before returning True.
A "healthy read" is defined as:
    - latency_p99 < verify_thresholds.latency_p99_max_ms
    - up == 1

If `timeout_s` expires before min_samples consecutive passes → returns False.
"""

import time

import requests

from engine.logger import JsonLogger

log = JsonLogger("verify")


def query_prometheus(prometheus_url: str, promql: str) -> float | None:
    """Execute an instant PromQL query; return scalar or None on error."""
    try:
        resp = requests.get(
            f"{prometheus_url}/api/v1/query",
            params={"query": promql},
            timeout=5,
        )
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("result", [])
        if results:
            return float(results[0]["value"][1])
    except Exception as exc:
        log.error("PROMETHEUS_QUERY_ERROR", query=promql, error=str(exc))
    return None


def verify_service(
    prometheus_url: str,
    service: str,
    baseline: dict,
    timeout_s: int,
    poll_interval_s: int,
    min_samples: int,
) -> bool:
    """
    Poll Prometheus until post-action metrics recover or timeout expires.

    Parameters
    ----------
    prometheus_url   : e.g. "http://localhost:9090"
    service          : service name as used in Prometheus labels
    baseline         : parsed baseline.json dict
    timeout_s        : maximum seconds to wait
    poll_interval_s  : seconds between polls
    min_samples      : number of consecutive passing samples required

    Returns
    -------
    True  → verified healthy (min_samples consecutive passes)
    False → timeout expired without enough consecutive passes
    """
    thresholds = baseline["verify_thresholds"]
    queries = baseline["prometheus_queries"]

    latency_q = queries["latency_p99"].replace("{service}", service)
    up_q = queries["up"].replace("{service}", service)

    deadline = time.time() + timeout_s
    passes = 0
    samples = 0

    log.info("VERIFY_START", service=service, timeout_s=timeout_s,
             latency_threshold_ms=thresholds["latency_p99_max_ms"],
             min_samples=min_samples)

    while time.time() < deadline:
        latency = query_prometheus(prometheus_url, latency_q)
        up = query_prometheus(prometheus_url, up_q)
        samples += 1

        latency_ok = latency is not None and latency < thresholds["latency_p99_max_ms"]
        up_ok = up is not None and up == thresholds["up_required"]

        log.info(
            "VERIFY_SAMPLE",
            service=service,
            sample=samples,
            latency_p99_ms=round(latency, 2) if latency is not None else None,
            up=up,
            latency_ok=latency_ok,
            up_ok=up_ok,
        )

        if latency_ok and up_ok:
            passes += 1
            if passes >= min_samples:
                log.info("VERIFY_PASS", service=service, samples=samples,
                         consecutive_passes=passes)
                return True
        else:
            passes = 0  # require consecutive passes

        time.sleep(poll_interval_s)

    log.warning("VERIFY_FAIL", service=service, samples=samples,
                timeout_s=timeout_s)
    return False
