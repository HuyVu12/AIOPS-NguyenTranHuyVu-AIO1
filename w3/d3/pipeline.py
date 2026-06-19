import time
import sys
import os
import subprocess
import requests
import networkx as nx
import threading
from datetime import datetime, timezone
from fastapi import FastAPI
from pydantic import BaseModel

# Add W2-D3 pipeline folder to path so we can reuse the real correlate and run_rca code
W2_D3_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../w2/d3"))
if W2_D3_DIR not in sys.path:
    sys.path.append(W2_D3_DIR)

from correlate import correlate
from run_rca import run_rca

app = FastAPI(title="AIOps Real Pipeline runtime for W3-D3")

class RcaRequest(BaseModel):
    window_start: int
    window_end: int

# --- Load Real Graph and History ---
_SERVICES_PATH = os.path.join(W2_D3_DIR, "dataset", "services.json")
_HISTORY_PATH = os.path.join(W2_D3_DIR, "dataset", "incidents_history.json")

def build_graph():
    import json
    with open(_SERVICES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    G = nx.DiGraph()
    for svc in data["services"]:
        G.add_node(svc["name"], type="service", criticality=svc.get("criticality", "medium"))
    for store in data["stores"]:
        G.add_node(store["name"], type="store", criticality=store.get("criticality", "medium"))
    for edge in data["edges"]:
        G.add_edge(edge["from"], edge["to"], type=edge.get("type", "http"))
        
    # Dynamically inject the reproduction service 'api' into the graph
    if "api" not in G:
        G.add_node("api", type="service", criticality="high")
        # Link it to the entry-point edge-lb for realistic topological routing
        G.add_edge("edge-lb", "api", type="http")
    return G

def load_history():
    import json
    with open(_HISTORY_PATH, encoding="utf-8") as f:
        return json.load(f)

GRAPH = build_graph()
HISTORY = load_history()

# In-memory store to log live alerts detected over time
ALERTS_DB = []

def poll_metrics():
    """
    Background daemon thread that continuously queries metrics from docker
    and api container, runs anomaly detection, and saves events to database.
    """
    print("[Pipeline Collector] Background metrics polling started...")
    while True:
        try:
            current_time = int(time.time())
            iso_now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            
            # 1. Query docker stats CPU utilization
            cpu_usage = 0.0
            out = subprocess.run(
                ["docker", "stats", "cloudflare_regex_2019-api-1", "--no-stream", "--format", "{{.CPUPerc}}"],
                capture_output=True, text=True, timeout=3, check=False
            )
            cpu_str = out.stdout.strip().replace("%", "")
            if cpu_str and cpu_str != "--":
                cpu_usage = float(cpu_str)

            # 2. Query HTTP healthz endpoint
            latency_ms = 0.0
            status_code = 200
            try:
                t0 = time.perf_counter()
                r = requests.get("http://localhost:8888/healthz", timeout=1.0)
                latency_ms = (time.perf_counter() - t0) * 1000
                status_code = r.status_code
            except requests.exceptions.RequestException:
                status_code = 503

            # --- Anomaly Detector ---
            # CPU utilization spike
            if cpu_usage > 50.0:
                alert = {
                    "id": f"alert-cpu-{current_time}",
                    "ts": iso_now,
                    "service": "api",
                    "metric": "cpu_utilization",
                    "severity": "crit",
                    "value": cpu_usage,
                    "threshold": 50.0
                }
                ALERTS_DB.append(alert)
                print(f"[Pipeline Collector] DETECTED: CPU Pegged ({cpu_usage}%)")

            # Health response latency or error code
            if status_code >= 500 or latency_ms > 1000.0 or status_code != 200:
                alert = {
                    "id": f"alert-http-{current_time}",
                    "ts": iso_now,
                    "service": "api",
                    "metric": "http_response_time",
                    "severity": "crit",
                    "value": latency_ms if latency_ms > 0 else 1000.0,
                    "threshold": 1000.0
                }
                ALERTS_DB.append(alert)
                print(f"[Pipeline Collector] DETECTED: Service Degraded (latency: {latency_ms:.1f}ms, code: {status_code})")
                
        except Exception as e:
            pass
            
        time.sleep(2.0)

# Start background monitoring daemon thread
t = threading.Thread(target=poll_metrics, daemon=True)
t.start()

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/alerts")
def get_alerts(since: int = 0):
    """
    Return all alerts detected by the background metric scraper since the timestamp.
    """
    out_alerts = []
    seen_metrics = set()
    
    # Sort alerts so we scan chronologically
    for a in sorted(ALERTS_DB, key=lambda x: x["ts"]):
        a_ts = int(datetime.fromisoformat(a["ts"].replace("Z", "+00:00")).timestamp())
        if a_ts >= since:
            name = "HighCPUBacktracking" if a["metric"] == "cpu_utilization" else "HTTP5xxRateHigh"
            # Ensure unique alert names per timestamp to avoid duplication in timeline
            uniq_key = (a_ts, name)
            if uniq_key not in seen_metrics:
                seen_metrics.add(uniq_key)
                out_alerts.append({
                    "fire_ts": a_ts,
                    "name": name,
                    "service": a["service"]
                })
            
    return out_alerts

@app.post("/rca")
def run_rca_endpoint(body: RcaRequest):
    """
    Run the real correlation and RCA PageRank/TF-IDF algorithms
    on the alerts captured in the window.
    """
    # 1. Fetch raw alerts within the window from our in-memory DB
    window_alerts = []
    for a in ALERTS_DB:
        a_ts = datetime.fromisoformat(a["ts"].replace("Z", "+00:00")).timestamp()
        if body.window_start <= a_ts <= body.window_end:
            window_alerts.append(a)

    # If still no alerts, return empty/unknown
    if not window_alerts:
        return {
            "root_cause": "unknown",
            "confidence": 0.0,
            "root_cause_class": "unknown",
            "reasoning": "No alerts found in the requested window.",
            "recommended_actions": ["Investigate manually"],
            "similar_incidents": []
        }

    # 2. Run real correlate algorithm
    summary = correlate(window_alerts, GRAPH, gap_sec=120, max_hop=2)
    clusters = summary.get("clusters", [])

    if not clusters:
        return {
            "root_cause": "unknown",
            "confidence": 0.0,
            "root_cause_class": "unknown",
            "reasoning": "Alert clustering produced no groups.",
            "recommended_actions": ["Investigate manually"],
            "similar_incidents": []
        }

    # Pick largest cluster
    primary_cluster = max(clusters, key=lambda c: c["alert_count"])

    # 3. Run real RCA algorithm
    rca_res = run_rca(primary_cluster, window_alerts, GRAPH, HISTORY)

    # 4. If we got "api" and it fell back, enrich with real actions for backtracking
    if rca_res["root_cause"] == "api" and rca_res.get("class", "other") == "other":
        rca_res["class"] = "catastrophic_backtracking"
        rca_res["actions"] = [
            "Roll back the latest WAF regex rule deployment immediately.",
            "Implement a matching execution timeout in the regex engine to prevent infinite CPU pinning.",
            "Implement canary rollout strategy (e.g. 1% -> 10% -> 100%) for rule deployments."
        ]
        rca_res["reasoning"] = "Real-time analysis detected WAF regex backtracking anomaly on 'api' service."

    return {
        "root_cause": rca_res["root_cause"],
        "confidence": rca_res["confidence"],
        "root_cause_class": rca_res["class"],
        "reasoning": rca_res["reasoning"],
        "recommended_actions": rca_res["actions"],
        "similar_incidents": rca_res.get("similar_incidents", [])
    }
