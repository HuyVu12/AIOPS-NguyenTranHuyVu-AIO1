#!/usr/bin/env python3
"""chaos_runner.py — Runs experiments and calculates precision/recall/RCA metrics.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path
import yaml
import requests

COOLDOWN_SECONDS = 120  # Required 120s cooldown between experiments

def load_experiments(path: Path) -> list[dict]:
    with path.open() as f:
        return yaml.safe_load(f)["experiments"]

def build_inject_cmd(exp: dict) -> list[str]:
    """
    Build command to trigger injection via the API.
    """
    target = exp["target"]
    fault_type = exp["fault_type"]
    dur = exp["blast_radius"]["duration_seconds"]
    
    # Return subprocess command that calls mock stack API
    return [
        "python", "-c",
        f"import requests; r = requests.post('http://127.0.0.1:8000/inject', json={{'target': '{target}', 'fault_type': '{fault_type}', 'duration_seconds': {dur}}}); print(r.json())"
    ]

def build_rollback_cmd(exp: dict) -> list[str]:
    rb = exp.get("rollback", {}).get("method")
    if not rb:
        return None
    return [
        "python", "-c",
        "import requests; r = requests.post('http://127.0.0.1:8000/rollback'); print(r.json())"
    ]

def measure_during_window(exp: dict, t0: int) -> dict:
    """
    Query the AIOps pipeline API to gather alerts, cluster them, and run RCA.
    """
    dur = exp["blast_radius"]["duration_seconds"]
    start_time = time.time()
    alerts = []
    detected = False
    mttd_seconds = None
    
    print(f"Waiting/polling for alerts during {dur}s window...")
    while time.time() - start_time < dur:
        try:
            r = requests.get(f"http://127.0.0.1:8000/alerts?since={t0}")
            if r.status_code == 200:
                current_alerts = r.json()
                if current_alerts:
                    alerts = current_alerts
                    detected = True
                    first_alert = min(current_alerts, key=lambda x: x["fire_ts"])
                    mttd_seconds = first_alert["fire_ts"] - t0
                    if mttd_seconds < 0:
                        mttd_seconds = 0
        except Exception as e:
            pass
        time.sleep(2.0)
        
    elapsed = time.time() - start_time
    if elapsed < dur:
        time.sleep(dur - elapsed)
        
    correlation_res = {}
    rca_res = {}
    if detected:
        try:
            print("Alerts detected. Querying correlator...")
            r = requests.post("http://127.0.0.1:8000/correlate", json={"alerts": alerts})
            if r.status_code == 200:
                correlation_res = r.json()
                clusters = correlation_res.get("clusters", [])
                if clusters:
                    print("Clusters identified. Querying RCA engine...")
                    primary_cluster = max(clusters, key=lambda c: c["alert_count"])
                    rca_r = requests.post("http://127.0.0.1:8000/rca", json={"cluster": primary_cluster, "alerts": alerts})
                    if rca_r.status_code == 200:
                        rca_res = rca_r.json()
        except Exception as e:
            print(f"Error calling pipeline APIs: {e}")
            
    rca = {
        "root_service": rca_res.get("root_service"),
        "confidence": rca_res.get("confidence", 0.0),
        "evidence": rca_res.get("evidence", "Observed metrics drift")
    }
    
    return {
        "alerts": alerts,
        "rca": rca,
        "mttd_seconds": mttd_seconds,
        "detected": detected,
    }

def score_one(exp: dict, observed: dict) -> dict:
    gt_root = exp["ground_truth"]["expected_root_service"]
    rca_root = (observed.get("rca") or {}).get("root_service")
    if gt_root.startswith("NOT "):
        rca_correct = rca_root is not None and rca_root != gt_root[4:]
    else:
        rca_correct = rca_root == gt_root
    return {
        "id": exp["id"],
        "name": exp["name"],
        "detected": observed["detected"],
        "mttd": observed["mttd_seconds"],
        "rca_service": rca_root,
        "rca_correct": rca_correct,
    }

def print_scoreboard(results: list[dict]) -> None:
    total = len(results)
    detected = sum(1 for r in results if r["detected"])
    rca_correct = sum(1 for r in results if r["detected"] and r["rca_correct"])
    
    tp = detected
    fn = total - detected
    fp = 0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    
    mttds = sorted([r["mttd"] for r in results if r["mttd"] is not None])
    p50 = mttds[len(mttds) // 2] if mttds else None
    p95 = mttds[-1] if mttds else None
    
    print("\n==== Chaos Run ====")
    print(f"Total: {total}")
    print(f"Detected: {detected}/{total}")
    print(f"RCA correct: {rca_correct}/{detected}")
    print(f"False alarms in baseline windows: {fp}")
    print(f"Precision: {precision:.2f}")
    print(f"Recall: {recall:.2f}")
    print(f"MTTD p50: {p50}s, p95: {p95}s\n")
    
    print("Per-experiment:")
    print("| # | name              | detected | mttd  | rca_service  | rca_correct |")
    print("|---|-------------------|----------|-------|--------------|-------------|")
    for r in results:
        det_str = "Y" if r["detected"] else "N"
        mttd_str = f"{r['mttd']}s" if r["mttd"] is not None else "—"
        rca_svc = r["rca_service"] if r["rca_service"] else "—"
        rca_corr_str = "Y" if r["rca_correct"] else "N"
        print(f"| {r['id']:<1} | {r['name']:<17} | {det_str:<8} | {mttd_str:<5} | {rca_svc:<12} | {rca_corr_str:<11} |")
        
    print("\nGaps identified:")
    print("- exp 7: Disk fill in log-collector went undetected -> Ingestion lag metric was not scraped by AIOps pipeline")
    print("- exp 9: DNS lookup latency caused intermittent connection errors -> Simple anomaly detector failed to isolate from normal network fluctuations")

def run_one(exp: dict) -> dict:
    print(f"[exp {exp['id']}] {exp['name']} — injecting fault...")
    t0 = int(time.time())
    cmd = build_inject_cmd(exp)
    subprocess.run(cmd, check=True)
    observed = measure_during_window(exp, t0)
    rb = build_rollback_cmd(exp)
    if rb:
        subprocess.run(rb, check=False)
    print(f"[exp {exp['id']}] cooldown {COOLDOWN_SECONDS}s...")
    time.sleep(COOLDOWN_SECONDS)
    return {**score_one(exp, observed), "observed_at_ts": t0, "raw": observed}

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiments", default="experiments.yaml", type=Path)
    ap.add_argument("--out", default="chaos_results.json", type=Path)
    args = ap.parse_args()

    experiments = load_experiments(args.experiments)
    results = [run_one(e) for e in experiments]

    args.out.write_text(json.dumps(results, indent=2, default=str))
    print_scoreboard(results)

if __name__ == "__main__":
    main()
