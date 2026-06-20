#!/usr/bin/env python3
"""mock_services.py — Standalone server simulating a 10-service cluster and its AIOps pipeline.
Runs HTTP servers on port 8000 and 8080.
"""
import sys
import os
import time
import threading
import json
import random
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

# Add W2-D3 pipeline folder to path so we can reuse the real correlate and run_rca code
W2_D3_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../w2/d3"))
if W2_D3_DIR not in sys.path:
    sys.path.append(W2_D3_DIR)

from correlate import correlate
from run_rca import run_rca

# --- Load Real Graph and History ---
_SERVICES_PATH = os.path.join(W2_D3_DIR, "dataset", "services.json")
_HISTORY_PATH = os.path.join(W2_D3_DIR, "dataset", "incidents_history.json")

with open(_SERVICES_PATH, encoding="utf-8") as f:
    services_data = json.load(f)

with open(_HISTORY_PATH, encoding="utf-8") as f:
    HISTORY = json.load(f)

# Rebuild the NetworkX topology graph
import networkx as nx
GRAPH = nx.DiGraph()
for svc in services_data["services"]:
    GRAPH.add_node(svc["name"], type="service", criticality=svc.get("criticality", "medium"))
for store in services_data["stores"]:
    GRAPH.add_node(store["name"], type="store", criticality=store.get("criticality", "medium"))
for edge in services_data["edges"]:
    GRAPH.add_edge(edge["from"], edge["to"], type=edge.get("type", "http"))

# --- Global Metrics & State Management ---
INITIAL_METRICS = {
    "payment-svc": {"latency": 20.0, "error_rate": 0.0, "availability": 1.0, "cpu": 15.0},
    "api-gateway": {"latency": 15.0, "error_rate": 0.0, "availability": 1.0, "cpu": 12.0},
    "inventory-svc": {"latency": 10.0, "error_rate": 0.0, "availability": 1.0, "cpu": 8.0},
    "checkout-svc": {"latency": 25.0, "error_rate": 0.0, "availability": 1.0, "cpu": 10.0},
    "auth-svc": {"latency": 12.0, "error_rate": 0.0, "availability": 1.0, "cpu": 5.0, "time_skew": 0.0},
    "log-collector": {"latency": 5.0, "error_rate": 0.0, "availability": 1.0, "disk_free": 95.0, "ingestion_lag": 0.0},
    "dns-resolver": {"latency": 2.0, "error_rate": 0.0, "availability": 1.0},
    "payment-db": {"cpu": 10.0, "memory": 25.0},
    "inventory-db": {"cpu": 8.0, "memory": 20.0},
    "frontend": {"connectivity": 1.0, "latency": 30.0, "error_rate": 0.0}
}

METRICS = json.loads(json.dumps(INITIAL_METRICS))
metrics_lock = threading.Lock()

active_injection = None  # {"target": ..., "fault_type": ..., "injected_at": ...}
ALERTS_DB = []

def reset_metrics():
    with metrics_lock:
        global METRICS
        METRICS = json.loads(json.dumps(INITIAL_METRICS))

# Delays mapping: (service, metric) -> seconds before alert is raised
DELAYS = {
    ("inventory-svc", "availability"): 12,
    ("api-gateway", "cpu"): 15,
    ("frontend", "connectivity"): 18,
    ("payment-svc", "error_rate"): 22,
    ("payment-svc", "latency"): 28,
    ("payment-db", "memory"): 32,
    ("checkout-svc", "error_rate"): 35,
    ("auth-svc", "time_skew"): 45,
}

def alert_evaluator_loop():
    print("[Mock System] Alert evaluator background thread started.")
    raised_alerts = set()
    
    while True:
        try:
            time.sleep(1.0)
            now = int(time.time())
            iso_now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            
            with metrics_lock:
                if active_injection is None:
                    raised_alerts.clear()
                    continue
                
                target = active_injection["target"]
                fault_type = active_injection["fault_type"]
                injected_at = active_injection["injected_at"]
                
                def try_raise_alert(alert_name, service, metric, value, threshold):
                    alert_key = (service, metric)
                    delay = DELAYS.get(alert_key, 10)
                    
                    if now - injected_at >= delay:
                        if alert_name not in raised_alerts:
                            raised_alerts.add(alert_name)
                            alert_obj = {
                                "id": f"alert-{service}-{metric}-{now}",
                                "ts": iso_now,
                                "fire_ts": now,
                                "name": alert_name,
                                "service": service,
                                "metric": metric,
                                "severity": "crit",
                                "value": float(value),
                                "threshold": float(threshold)
                            }
                            ALERTS_DB.append(alert_obj)
                            print(f"[Mock System] Alert FIRED: {alert_name} on {service} at {iso_now} (delay {now - injected_at}s)")
                
                # Check metrics against static threshold rules
                
                # 1. Latency trigger on payment-svc
                if METRICS["payment-svc"]["latency"] > 200.0:
                    try_raise_alert("payment_latency_alert", "payment-svc", "latency", METRICS["payment-svc"]["latency"], 200.0)
                    
                # 2. Error rate trigger on payment-svc
                if METRICS["payment-svc"]["error_rate"] > 10.0:
                    try_raise_alert("payment_loss_alert", "payment-svc", "error_rate", METRICS["payment-svc"]["error_rate"], 10.0)
                    
                # 3. Availability trigger on inventory-svc
                if METRICS["inventory-svc"]["availability"] < 0.5:
                    try_raise_alert("inventory_kill_alert", "inventory-svc", "availability", METRICS["inventory-svc"]["availability"], 0.5)
                    
                # 4. CPU trigger on api-gateway
                if METRICS["api-gateway"]["cpu"] > 80.0:
                    try_raise_alert("gateway_cpu_alert", "api-gateway", "cpu", METRICS["api-gateway"]["cpu"], 80.0)
                    
                # 5. Memory trigger on payment-db
                if "memory" in METRICS["payment-db"] and METRICS["payment-db"]["memory"] > 90.0:
                    try_raise_alert("db_memory_alert", "payment-db", "memory", METRICS["payment-db"]["memory"], 90.0)
                    
                # 6. Time skew trigger on auth-svc
                if "time_skew" in METRICS["auth-svc"] and METRICS["auth-svc"]["time_skew"] > 30.0:
                    try_raise_alert("auth_skew_alert", "auth-svc", "time_skew", METRICS["auth-svc"]["time_skew"], 30.0)
                    
                # 7. Frontend partition trigger (frontend connectivity)
                if METRICS["frontend"]["connectivity"] == 0.0:
                    try_raise_alert("gateway_partition_alert", "frontend", "connectivity", METRICS["frontend"]["connectivity"], 0.5)
                    
                # 8. Checkout retry storm trigger (checkout error rate)
                if METRICS["checkout-svc"]["error_rate"] > 10.0:
                    try_raise_alert("checkout_retry_storm_alert", "checkout-svc", "error_rate", METRICS["checkout-svc"]["error_rate"], 10.0)
                    if METRICS["payment-svc"]["cpu"] > 80.0:
                        try_raise_alert("payment_retry_load_alert", "payment-svc", "cpu", METRICS["payment-svc"]["cpu"], 80.0)
                    if METRICS["inventory-svc"]["cpu"] > 75.0:
                        try_raise_alert("inventory_retry_load_alert", "inventory-svc", "cpu", METRICS["inventory-svc"]["cpu"], 75.0)

        except Exception as e:
            print(f"[Mock System Loop Error] {e}")

# Start background evaluation loop
t = threading.Thread(target=alert_evaluator_loop, daemon=True)
t.start()

# --- Port 8000: AIOps Pipeline Server ---
app_8000 = FastAPI(title="AIOps Pipeline API Simulator (Port 8000)")

class InjectRequest(BaseModel):
    target: str
    fault_type: str
    duration_seconds: int

@app_8000.post("/inject")
def inject_fault(body: InjectRequest):
    global active_injection
    reset_metrics()
    
    with metrics_lock:
        active_injection = {
            "target": body.target,
            "fault_type": body.fault_type,
            "injected_at": int(time.time())
        }
        
        target = body.target
        fault_type = body.fault_type
        
        if fault_type == "latency":
            METRICS[target]["latency"] = 500.0
            if target == "payment-svc":
                METRICS["api-gateway"]["latency"] = 520.0
                METRICS["checkout-svc"]["latency"] = 530.0
        elif fault_type == "network_loss":
            METRICS[target]["error_rate"] = 30.0
            METRICS[target]["latency"] = 150.0
            if target == "payment-svc":
                METRICS["checkout-svc"]["error_rate"] = 15.0
        elif fault_type == "availability":
            METRICS[target]["availability"] = 0.0
            if target == "inventory-svc":
                METRICS["checkout-svc"]["error_rate"] = 2.0
        elif fault_type == "cpu_saturation":
            METRICS[target]["cpu"] = 90.0
            METRICS[target]["latency"] = 1500.0
            if target == "api-gateway":
                METRICS["frontend"]["latency"] = 1550.0
        elif fault_type == "memory":
            METRICS[target]["memory"] = 95.0
            if target == "payment-db":
                METRICS["payment-svc"]["latency"] = 800.0
        elif fault_type == "time_skew":
            METRICS[target]["time_skew"] = 60.0
            if target == "auth-svc":
                METRICS["api-gateway"]["error_rate"] = 40.0
        elif fault_type == "disk_fill":
            METRICS[target]["disk_free"] = 5.0
            METRICS[target]["ingestion_lag"] = 120.0
        elif fault_type == "network_partition":
            METRICS["frontend"]["connectivity"] = 0.0
            METRICS["frontend"]["error_rate"] = 100.0
        elif fault_type == "dns_latency":
            METRICS[target]["latency"] = 2000.0
            METRICS["api-gateway"]["latency"] = 800.0
            METRICS["api-gateway"]["error_rate"] = 5.0
        elif fault_type == "cascade_retry" or fault_type == "http_error":
            METRICS["checkout-svc"]["error_rate"] = 20.0
            METRICS["payment-svc"]["cpu"] = 85.0
            METRICS["inventory-svc"]["cpu"] = 80.0
            
    print(f"[Mock System] Injected fault '{fault_type}' on '{target}' for {body.duration_seconds}s")
    return {"status": "ok", "message": f"Injected {fault_type} on {target}"}

@app_8000.post("/rollback")
def rollback_fault():
    global active_injection
    reset_metrics()
    active_injection = None
    print("[Mock System] Rolled back all faults")
    return {"status": "ok", "message": "Rolled back all faults"}

@app_8000.get("/alerts")
def get_alerts(since: int = 0):
    res = []
    for a in ALERTS_DB:
        if a["fire_ts"] >= since:
            res.append({
                "fire_ts": a["fire_ts"],
                "name": a["name"],
                "service": a["service"]
            })
    return res

class CorrelateRequest(BaseModel):
    alerts: list[dict]

@app_8000.post("/correlate")
def run_correlation(body: CorrelateRequest):
    alerts_for_correlate = []
    for index, a in enumerate(body.alerts):
        metric = "unknown"
        if "latency" in a["name"]:
            metric = "latency"
        elif "loss" in a["name"]:
            metric = "error_rate"
        elif "kill" in a["name"]:
            metric = "availability"
        elif "cpu" in a["name"] or "retry" in a["name"]:
            metric = "cpu"
        elif "memory" in a["name"]:
            metric = "memory"
        elif "skew" in a["name"]:
            metric = "time_skew"
        elif "partition" in a["name"]:
            metric = "connectivity"
            
        alerts_for_correlate.append({
            "id": f"alert-corr-{index}-{int(time.time())}",
            "ts": datetime.fromtimestamp(a["fire_ts"], tz=timezone.utc).isoformat(),
            "service": a["service"],
            "metric": metric,
            "severity": "crit",
            "value": 1.0,
            "threshold": 0.5
        })
    
    if not alerts_for_correlate:
        return {"clusters": [], "input_alerts": 0, "output_clusters": 0, "reduction_ratio": 0.0}
        
    summary = correlate(alerts_for_correlate, GRAPH, gap_sec=120, max_hop=2)
    return summary

class RcaRequest(BaseModel):
    cluster: dict
    alerts: list[dict]

@app_8000.post("/rca")
def run_rca_endpoint(body: RcaRequest):
    alerts_for_rca = []
    for index, a in enumerate(body.alerts):
        metric = "unknown"
        if "latency" in a["name"]:
            metric = "latency"
        elif "loss" in a["name"]:
            metric = "error_rate"
        elif "kill" in a["name"]:
            metric = "availability"
        elif "cpu" in a["name"] or "retry" in a["name"]:
            metric = "cpu"
        elif "memory" in a["name"]:
            metric = "memory"
        elif "skew" in a["name"]:
            metric = "time_skew"
        elif "partition" in a["name"]:
            metric = "connectivity"
        
        alerts_for_rca.append({
            "id": f"alert-rca-{index}",
            "ts": datetime.fromtimestamp(a["fire_ts"], tz=timezone.utc).isoformat(),
            "service": a["service"],
            "metric": metric,
            "severity": "crit",
            "value": 1.0,
            "threshold": 0.5
        })
        
    rca_res = run_rca(body.cluster, alerts_for_rca, GRAPH, HISTORY)
    
    return {
        "root_service": rca_res["root_cause"],
        "confidence": rca_res["confidence"],
        "evidence": rca_res["reasoning"]
    }

# --- Port 8080: Mock Services Web Stack ---
app_8080 = FastAPI(title="Mock Cluster Web API (Port 8080)")

@app_8080.get("/checkout/health")
def checkout_health():
    with metrics_lock:
        latency = METRICS["checkout-svc"]["latency"]
        error_rate = METRICS["checkout-svc"]["error_rate"]
        availability = METRICS["checkout-svc"]["availability"]
        connectivity = METRICS["frontend"]["connectivity"]
        
    if connectivity == 0.0 or availability == 0.0:
        time.sleep(1.0)
        raise HTTPException(status_code=504, detail="Gateway Timeout")
        
    if latency > 100.0:
        time.sleep(min(latency / 1000.0, 5.0))
        
    if error_rate > 0.0:
        if random.random() * 100.0 < error_rate:
            raise HTTPException(status_code=500, detail="Internal Server Error")
            
    return "pass"

def run_8000():
    uvicorn.run(app_8000, host="127.0.0.1", port=8000, log_level="warning")

def run_8080():
    uvicorn.run(app_8080, host="127.0.0.1", port=8080, log_level="warning")

if __name__ == "__main__":
    t1 = threading.Thread(target=run_8000)
    t2 = threading.Thread(target=run_8080)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
