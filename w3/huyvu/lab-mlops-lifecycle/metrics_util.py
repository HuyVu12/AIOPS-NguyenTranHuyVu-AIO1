"""metrics_util — push MLOps lifecycle metrics to Prometheus Pushgateway.

Requires prometheus_client.
Uses PUSHGATEWAY_URL from environment (default: http://localhost:9091).
Uses pushadd_to_gateway to ensure metrics are merged rather than overwritten.
"""

import os
from prometheus_client import CollectorRegistry, Counter, Gauge, pushadd_to_gateway

PUSHGATEWAY_URL = os.environ.get("PUSHGATEWAY_URL", "http://localhost:9091")

def _registry() -> CollectorRegistry:
    return CollectorRegistry()

def push_drift_score(score: float, threshold: float) -> None:
    """Push current drift score and threshold to pushgateway (job=drift_detector)."""
    reg = _registry()
    g_score = Gauge("mlops_drift_score", "Fraction of features drifted (0-1)", registry=reg)
    g_thresh = Gauge("mlops_drift_threshold", "Configured drift threshold", registry=reg)
    g_flag = Gauge("mlops_drift_is_drift", "1 if drift detected, 0 otherwise", registry=reg)
    
    g_score.set(score)
    g_thresh.set(threshold)
    g_flag.set(1.0 if score > threshold else 0.0)
    
    try:
        pushadd_to_gateway(PUSHGATEWAY_URL, job="drift_detector", registry=reg)
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")

def push_model_eval(version: str, precision: float, recall: float, f1: float) -> None:
    """Push per-version precision/recall/f1 metrics to pushgateway (job=retrain)."""
    reg = _registry()
    labels = ["version"]
    g_p = Gauge("mlops_model_precision", "Model precision on eval set", labels, registry=reg)
    g_r = Gauge("mlops_model_recall", "Model recall on eval set", labels, registry=reg)
    g_f = Gauge("mlops_model_f1", "Model F1 on eval set", labels, registry=reg)
    
    g_p.labels(version=version).set(precision)
    g_r.labels(version=version).set(recall)
    g_f.labels(version=version).set(f1)
    
    try:
        pushadd_to_gateway(PUSHGATEWAY_URL, job="retrain", registry=reg)
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")

def push_event(event_type: str, version: str) -> None:
    """Increment a lifecycle event counter in Pushgateway (job=retrain).

    event_type: 'retrain_triggered' | 'auto_rollback_v2_to_v1'
    """
    reg = _registry()
    c = Counter(
        f"mlops_{event_type}_total",
        f"Total count of {event_type} events",
        ["version"],
        registry=reg,
    )
    c.labels(version=version).inc()
    
    try:
        pushadd_to_gateway(PUSHGATEWAY_URL, job="retrain", registry=reg)
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")

def push_active_version(version: str, alias: str) -> None:
    """Push the active model version mapping for the given alias (job=retrain)."""
    reg = _registry()
    g_num = Gauge(
        "mlops_active_version_number",
        "Integer version number for the given alias",
        ["alias"],
        registry=reg,
    )
    g_info = Gauge(
        "mlops_active_version_info",
        "Label-only gauge representing alias->version mapping",
        ["alias", "version"],
        registry=reg,
    )
    
    try:
        g_num.labels(alias=alias).set(int(version))
    except ValueError:
        g_num.labels(alias=alias).set(0)
        
    g_info.labels(alias=alias, version=version).set(1.0)
    
    try:
        pushadd_to_gateway(PUSHGATEWAY_URL, job="retrain", registry=reg)
    except Exception as exc:
        print(f"[metrics_util] WARNING: pushgateway unreachable — {exc}")
