from datetime import datetime

def parse_ts(ts_str: str) -> datetime:
    """Parse ISO 8601 string to a datetime object with timezone info."""
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

def get_all_historical_log_signatures(history: list[dict]) -> list[str]:
    """Gather all unique log signatures from historical corpus."""
    signatures = set()
    for incident in history:
        for sig in incident.get("log_signatures", []):
            signatures.add(sig)
    return sorted(list(signatures))

def match_log_signature(raw_msg: str, signature: str) -> bool:
    """Check if a raw log message matches a historical log signature template using keywords."""
    # Split signature by colon and clean parts
    parts = [p.strip().lower() for p in signature.split(":") if p.strip()]
    if not parts:
        return False
    msg_lower = raw_msg.lower()
    return all(part in msg_lower for part in parts)

def find_affected_services(incident: dict) -> set[str]:
    """Identify services that are affected by anomalies during the incident."""
    affected = set()
    
    # 1. Alert trigger service is always affected
    trigger = incident.get("trigger_alert", {})
    if trigger and "service" in trigger:
        affected.add(trigger["service"])
        
    # 2. Services with a high count of error/fatal logs (>= 3)
    error_counts = {}
    for log in incident.get("logs", []):
        if log.get("level") in ("ERROR", "FATAL"):
            svc = log.get("svc")
            if svc:
                error_counts[svc] = error_counts.get(svc, 0) + 1
    for svc, count in error_counts.items():
        if count >= 3:
            affected.add(svc)
            
    # 3. Services involved in trace connections with high error rate (>= 5%)
    for trace in incident.get("traces", []):
        count = trace.get("count", 0)
        errors = trace.get("error_count", 0)
        if count > 0 and (errors / count) >= 0.05:
            if "from" in trace:
                affected.add(trace["from"])
            if "to" in trace:
                affected.add(trace["to"])
                
    return affected

def extract_log_features(logs: list[dict], all_signatures: list[str]) -> dict[str, int]:
    """Produce binary vectors indicating presence of historical signatures in raw logs."""
    features = {sig: 0 for sig in all_signatures}
    for log in logs:
        msg = log.get("msg", "")
        for sig in all_signatures:
            if match_log_signature(msg, sig):
                features[sig] = 1
    return features

def extract_trace_features(traces: list[dict], detected_at_str: str) -> dict[tuple[str, str], dict]:
    """Compute error rates and P99 response time deviations on connections."""
    detected_at = parse_ts(detected_at_str)
    
    edges_data = {}
    for t in traces:
        edge = (t["from"], t["to"])
        if edge not in edges_data:
            edges_data[edge] = {
                "baseline_p99": [],
                "anomaly_p99": [],
                "anomaly_errors": 0,
                "anomaly_count": 0
            }
            
        t_time = parse_ts(t["ts"])
        p99 = t.get("p99_ms", 0.0)
        
        if t_time < detected_at:
            edges_data[edge]["baseline_p99"].append(p99)
        else:
            edges_data[edge]["anomaly_p99"].append(p99)
            edges_data[edge]["anomaly_errors"] += t.get("error_count", 0)
            edges_data[edge]["anomaly_count"] += t.get("count", 0)
            
    features = {}
    for edge, data in edges_data.items():
        base_list = data["baseline_p99"]
        avg_base = sum(base_list) / len(base_list) if base_list else 100.0
        
        anom_list = data["anomaly_p99"]
        avg_anom = sum(anom_list) / len(anom_list) if anom_list else avg_base
        
        deviation = (avg_anom / avg_base) if avg_base > 0 else 1.0
        
        err_rate = 0.0
        if data["anomaly_count"] > 0:
            err_rate = data["anomaly_errors"] / data["anomaly_count"]
            
        features[edge] = {
            "p99_deviation_ratio": round(deviation, 2),
            "error_rate": round(err_rate, 2)
        }
    return features

def extract_metric_features(metrics_window: dict, detected_at_str: str) -> dict[tuple[str, str], dict]:
    """Calculate baseline and anomaly window average ratios for metrics."""
    detected_at = parse_ts(detected_at_str)
    samples = metrics_window.get("samples", {})
    
    features = {}
    for key, points in samples.items():
        if not points:
            continue
            
        parts = key.split(".")
        if len(parts) != 2:
            continue
        service, metric_name = parts[0], parts[1]
        
        baseline_vals = []
        anomaly_vals = []
        
        for ts_str, val in points:
            pt_time = parse_ts(ts_str)
            if pt_time < detected_at:
                baseline_vals.append(val)
            else:
                anomaly_vals.append(val)
                
        avg_base = sum(baseline_vals) / len(baseline_vals) if baseline_vals else 1.0
        avg_anom = sum(anomaly_vals) / len(anomaly_vals) if anomaly_vals else avg_base
        
        ratio = (avg_anom / avg_base) if avg_base > 0 else 1.0
        
        features[(service, metric_name)] = {
            "before": round(avg_base, 2),
            "after": round(avg_anom, 2),
            "ratio": round(ratio, 2)
        }
    return features

def extract_features(incident: dict, history: list[dict]) -> dict:
    """Main entry point to transform live incident raw evidence into an Incident Vector."""
    all_sigs = get_all_historical_log_signatures(history)
    detected_at = incident.get("detected_at", "")
    
    affected = find_affected_services(incident)
    logs_feat = extract_log_features(incident.get("logs", []), all_sigs)
    traces_feat = extract_trace_features(incident.get("traces", []), detected_at)
    metrics_feat = extract_metric_features(incident.get("metrics_window", {}), detected_at)
    
    return {
        "incident_id": incident.get("incident_id"),
        "trigger_alert": incident.get("trigger_alert", {}),
        "affected_services": affected,
        "log_features": logs_feat,
        "trace_features": traces_feat,
        "metric_features": metrics_feat
    }
