"""
AIOps W1 Individual Lab — Streaming Anomaly Pipeline
Approach: Z-score sliding window + rule-based log correlation
"""

from fastapi import FastAPI, Request
from collections import deque
import json
import math
import os
import uvicorn

app = FastAPI()
ALERTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerts.jsonl")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WINDOW_SIZE   = 30   # số datapoints giữ để tính baseline
WARMUP_TICKS  = 20   # chờ đủ data trước khi detect

ZSCORE_WARNING  = 2.5
ZSCORE_CRITICAL = 4.0

MEMORY_UTIL_WARNING  = 0.70
MEMORY_UTIL_CRITICAL = 0.85
UPSTREAM_TIMEOUT_WARNING  = 3.0    # % tuyệt đối — normal max ~0.4%
UPSTREAM_TIMEOUT_CRITICAL = 15.0

ALERT_COOLDOWN_TICKS = 20  # ~10s real-time giữa 2 alert cùng type

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
windows: dict = {
    "cpu_usage_percent":     deque(maxlen=WINDOW_SIZE),
    "http_requests_per_sec": deque(maxlen=WINDOW_SIZE),
    "http_p99_latency_ms":   deque(maxlen=WINDOW_SIZE),
    "http_5xx_rate":         deque(maxlen=WINDOW_SIZE),
    "jvm_gc_pause_ms_avg":   deque(maxlen=WINDOW_SIZE),
    "queue_depth":           deque(maxlen=WINDOW_SIZE),
    "upstream_timeout_rate": deque(maxlen=WINDOW_SIZE),
}

tick_count = 0
last_alert_time: dict = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mean(data):
    return sum(data) / len(data)

def _stdev(data, mu=None):
    if len(data) < 2:
        return 1e-9
    if mu is None:
        mu = _mean(data)
    v = sum((x - mu) ** 2 for x in data) / (len(data) - 1)
    return math.sqrt(v) if v > 0 else 1e-9

def zscore(value, data):
    if len(data) < 5:
        return 0.0
    mu = _mean(data)
    return (value - mu) / _stdev(data, mu)

def write_alert(alert: dict):
    with open(ALERTS_FILE, "a") as f:
        f.write(json.dumps(alert) + "\n")
    print(f"[ALERT] {alert['type']} | {alert['severity']} | {alert['message']}")

def can_alert(alert_type: str) -> bool:
    return (tick_count - last_alert_time.get(alert_type, -999)) >= ALERT_COOLDOWN_TICKS

def record_alert(alert_type: str):
    last_alert_time[alert_type] = tick_count

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_memory_leak(metrics, logs, timestamp):
    mem_util = metrics["memory_usage_bytes"] / metrics["memory_limit_bytes"]
    gc       = metrics["jvm_gc_pause_ms_avg"]
    gc_z     = zscore(gc, windows["jvm_gc_pause_ms_avg"])

    if mem_util >= MEMORY_UTIL_CRITICAL and gc_z > ZSCORE_WARNING:
        if can_alert("memory_leak"):
            write_alert({"timestamp": timestamp, "type": "memory_leak", "severity": "critical",
                "message": f"Memory at {mem_util*100:.1f}%, GC pause={gc:.0f}ms (z={gc_z:.1f})"})
            record_alert("memory_leak")
    elif mem_util >= MEMORY_UTIL_WARNING and gc_z > ZSCORE_WARNING:
        if can_alert("memory_leak"):
            write_alert({"timestamp": timestamp, "type": "memory_leak", "severity": "warning",
                "message": f"Memory at {mem_util*100:.1f}%, GC pressure gc_pause={gc:.0f}ms"})
            record_alert("memory_leak")

    for log in logs:
        if log.get("level") in ("ERROR", "FATAL") and "memory" in log.get("message", "").lower():
            if can_alert("memory_leak"):
                write_alert({"timestamp": timestamp, "type": "memory_leak", "severity": "critical",
                    "message": f"Log: {log['message']}"})
                record_alert("memory_leak")
            break


def detect_traffic_spike(metrics, logs, timestamp):
    rps = metrics["http_requests_per_sec"]
    qd  = metrics["queue_depth"]
    lat = metrics["http_p99_latency_ms"]

    rps_z = zscore(rps, windows["http_requests_per_sec"])
    qd_z  = zscore(qd,  windows["queue_depth"])
    lat_z = zscore(lat, windows["http_p99_latency_ms"])

    # Threshold cứng — bắt được ngay khi không có baseline
    if rps > 400 and lat > 800:
        if can_alert("traffic_spike"):
            write_alert({"timestamp": timestamp, "type": "traffic_spike", "severity": "critical",
                "message": f"Traffic spike: rps={rps:.0f}, latency={lat:.0f}ms, queue={qd}"})
            record_alert("traffic_spike")
    elif rps > 250 and lat > 300:
        if can_alert("traffic_spike"):
            write_alert({"timestamp": timestamp, "type": "traffic_spike", "severity": "warning",
                "message": f"Traffic rising: rps={rps:.0f}, latency={lat:.0f}ms, queue={qd}"})
            record_alert("traffic_spike")
    # Z-score khi đã có baseline
    elif rps_z > ZSCORE_CRITICAL and lat_z > ZSCORE_WARNING:
        if can_alert("traffic_spike"):
            write_alert({"timestamp": timestamp, "type": "traffic_spike", "severity": "critical",
                "message": f"Traffic spike: rps={rps:.0f} (z={rps_z:.1f}), latency={lat:.0f}ms, queue={qd}"})
            record_alert("traffic_spike")
    elif rps_z > ZSCORE_WARNING and qd_z > ZSCORE_WARNING:
        if can_alert("traffic_spike"):
            write_alert({"timestamp": timestamp, "type": "traffic_spike", "severity": "warning",
                "message": f"Traffic rising: rps={rps:.0f} (z={rps_z:.1f}), queue={qd} (z={qd_z:.1f})"})
            record_alert("traffic_spike")


def detect_dependency_timeout(metrics, logs, timestamp):
    ut       = metrics["upstream_timeout_rate"]
    err_rate = metrics["http_5xx_rate"]
    lat      = metrics["http_p99_latency_ms"]

    ut_z  = zscore(ut,  windows["upstream_timeout_rate"])
    lat_z = zscore(lat, windows["http_p99_latency_ms"])

    if ut >= UPSTREAM_TIMEOUT_CRITICAL or (ut_z > ZSCORE_CRITICAL and err_rate > 5):
        if can_alert("dependency_timeout"):
            write_alert({"timestamp": timestamp, "type": "dependency_timeout", "severity": "critical",
                "message": f"Upstream timeout={ut:.1f}% (z={ut_z:.1f}), 5xx={err_rate:.1f}%, latency={lat:.0f}ms"})
            record_alert("dependency_timeout")
    elif ut >= UPSTREAM_TIMEOUT_WARNING and ut_z > ZSCORE_WARNING:
        if can_alert("dependency_timeout"):
            write_alert({"timestamp": timestamp, "type": "dependency_timeout", "severity": "warning",
                "message": f"Upstream timeout rising: {ut:.1f}% (z={ut_z:.1f})"})
            record_alert("dependency_timeout")

    for log in logs:
        msg = log.get("message", "").lower()
        if log.get("level") in ("ERROR", "FATAL") and ("circuit" in msg or "timeout" in msg):
            if can_alert("dependency_timeout"):
                write_alert({"timestamp": timestamp, "type": "dependency_timeout", "severity": "critical",
                    "message": f"Log: {log['message']}"})
                record_alert("dependency_timeout")
            break

# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.post("/ingest")
async def ingest(request: Request):
    global tick_count
    payload   = await request.json()
    metrics   = payload["metrics"]
    logs      = payload["logs"]
    timestamp = payload["timestamp"]

    # Print gọn để theo dõi
    mem_pct = metrics["memory_usage_bytes"] / metrics["memory_limit_bytes"] * 100
    print(f"[tick={tick_count:04d}] mem={mem_pct:.1f}% cpu={metrics['cpu_usage_percent']}% "
          f"rps={metrics['http_requests_per_sec']} lat={metrics['http_p99_latency_ms']}ms "
          f"ut={metrics['upstream_timeout_rate']}% gc={metrics['jvm_gc_pause_ms_avg']}ms")

    # Cập nhật windows
    for key in windows:
        if key in metrics:
            windows[key].append(metrics[key])

    tick_count += 1

    if tick_count < WARMUP_TICKS:
        # Vẫn chạy traffic_spike detection ngay cả khi warmup (dùng threshold cứng)
        detect_traffic_spike(metrics, logs, timestamp)
        return {"status": "ok", "mode": "warmup"}

    # Detection
    detect_memory_leak(metrics, logs, timestamp)
    detect_traffic_spike(metrics, logs, timestamp)
    detect_dependency_timeout(metrics, logs, timestamp)

    return {"status": "ok", "tick": tick_count}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
