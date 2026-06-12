from features import extract_features

def calculate_similarity(query: dict, hist: dict) -> float:
    """Compute the combined similarity score between the query and a historical incident."""
    # 1. Log Similarity (Jaccard-like overlap of signatures)
    hist_logs = hist.get("log_signatures", [])
    if not hist_logs:
        s_log = 0.0
    else:
        matched = sum(1 for sig in hist_logs if query.get("log_features", {}).get(sig, 0) > 0)
        s_log = matched / len(hist_logs)
        
    # 2. Affected Services Similarity (Jaccard Index)
    query_svcs = set(query.get("affected_services", []))
    hist_svcs = set(hist.get("affected_services", []))
    if not query_svcs and not hist_svcs:
        s_svc = 1.0
    elif not query_svcs or not hist_svcs:
        s_svc = 0.0
    else:
        s_svc = len(query_svcs & hist_svcs) / len(query_svcs | hist_svcs)
        
    # 3. Trace Similarity (Jaccard of anomalous edges + MAE on common anomalous edges)
    hist_traces = { (t["from"], t["to"]): t for t in hist.get("trace_signatures", []) }
    query_traces = query.get("trace_features", {})
    
    hist_anom = set(hist_traces.keys())
    query_anom = {
        edge for edge, feat in query_traces.items()
        if feat.get("error_rate", 0.0) >= 0.05 or feat.get("p99_deviation_ratio", 1.0) >= 1.5
    }
    
    if not hist_anom:
        s_trace = 1.0
    elif not query_anom:
        s_trace = 0.0
    else:
        # Jaccard overlap of anomalous trace edges
        s_trace_jaccard = len(hist_anom & query_anom) / len(hist_anom | query_anom)
        common_edges = hist_anom & query_anom
        if common_edges:
            errors = []
            for edge in common_edges:
                h_err = hist_traces[edge].get("error_rate", 0.0)
                q_err = query_traces[edge].get("error_rate", 0.0)
                errors.append(abs(h_err - q_err))
                
                h_dev = hist_traces[edge].get("p99_deviation_ratio", 1.0)
                q_dev = query_traces[edge].get("p99_deviation_ratio", 1.0)
                errors.append(abs(h_dev - q_dev) / max(h_dev, q_dev, 1.0))
            mae = sum(errors) / len(errors)
            s_trace_val = max(0.0, 1.0 - mae)
        else:
            s_trace_val = 0.1
        s_trace = s_trace_jaccard * s_trace_val
        
    # Combined score
    total_score = 0.5 * s_log + 0.3 * s_trace + 0.2 * s_svc
    return round(total_score, 4)

def parse_and_normalize_action(action_str: str) -> dict:
    """Parse history action string to matching action name and params schema."""
    parts = action_str.split(":")
    if not parts:
        return {"name": "page_oncall", "params": {}}
    name = parts[0]
    params = {}
    if name == "rollback_service" and len(parts) >= 2:
        params["service"] = parts[1]
        params["target_version"] = "previous"
    elif name == "increase_pool_size" and len(parts) >= 2:
        params["service"] = parts[1]
    elif name == "restart_pod" and len(parts) >= 2:
        params["service"] = parts[1]
    elif name == "dns_config_rollback":
        pass
    elif name == "page_oncall" and len(parts) >= 2:
        params["team"] = parts[1]
    elif name == "network_policy_revert" and len(parts) >= 2:
        params["policy_name"] = parts[1]
    return {"name": name, "params": params}

def vote_candidates(neighbors: list[tuple[dict, float]], query_feat: dict) -> list[dict]:
    """Perform outcome-weighted voting over the actions of nearest neighbors with dynamic service mapping."""
    votes = {}
    query_affected = query_feat.get("affected_services", set())
    trigger_svc = query_feat.get("trigger_alert", {}).get("service")
    
    for hist, sim in neighbors:
        outcome = hist.get("outcome", "success")
        if outcome == "success":
            w = 1.0
        elif outcome == "partial":
            w = 0.5
        else:
            w = 0.0
            
        hist_affected = hist.get("affected_services", [])
        
        for act_str in hist.get("actions_taken", []):
            normalized = parse_and_normalize_action(act_str)
            
            # Map historical service parameter to query service if needed
            if "params" in normalized and "service" in normalized["params"]:
                s = normalized["params"]["service"]
                if s not in query_affected and s in hist_affected:
                    # Map to the query's primary affected service
                    if trigger_svc and trigger_svc in query_affected:
                        normalized["params"]["service"] = trigger_svc
                    elif query_affected:
                        normalized["params"]["service"] = sorted(list(query_affected))[0]
            
            key = (normalized["name"], frozenset(normalized["params"].items()))
            if key not in votes:
                votes[key] = {"success_sum": 0.0, "total_sum": 0.0, "max_sim": 0.0}
                
            votes[key]["success_sum"] += sim * w
            votes[key]["total_sum"] += sim
            if sim > votes[key]["max_sim"]:
                votes[key]["max_sim"] = sim
            
    candidate_list = []
    for (name, params_fs), data in votes.items():
        success_rate = data["success_sum"] / data["total_sum"] if data["total_sum"] > 0 else 0.0
        voting_score = success_rate * data["max_sim"]
        candidate_list.append({
            "name": name,
            "params": dict(params_fs),
            "voting_score": round(voting_score, 4)
        })
        
    candidate_list.sort(key=lambda x: x["voting_score"], reverse=True)
    return candidate_list

def retrieve_and_vote(query_feat: dict, history: list[dict], top_k: int = 3) -> dict:
    """Run KNN over the historical incident corpus and vote on action recommendations."""
    scored_history = []
    for hist in history:
        sim = calculate_similarity(query_feat, hist)
        scored_history.append((hist, sim))
        
    scored_history.sort(key=lambda x: x[1], reverse=True)
    max_similarity = scored_history[0][1] if scored_history else 0.0
    
    # 2. Out-of-Distribution (OOD) detection
    if max_similarity < 0.35:
        return {
            "is_ood": True,
            "max_similarity": max_similarity,
            "candidates": [
                {"name": "page_oncall", "params": {"team": "platform-team"}, "voting_score": 1.0}
            ],
            "top_neighbors": [scored_history[i][0]["id"] for i in range(min(top_k, len(scored_history)))]
        }
        
    # 3. K-Nearest Neighbors
    neighbors = scored_history[:top_k]
    candidates = vote_candidates(neighbors, query_feat)
    
    # Fallback to page_oncall if all voting scores are <= 0 or empty
    if not candidates or all(c["voting_score"] <= 0 for c in candidates):
        candidates = [
            {"name": "page_oncall", "params": {"team": "platform-team"}, "voting_score": 0.1}
        ]
        
    return {
        "is_ood": False,
        "max_similarity": max_similarity,
        "candidates": candidates,
        "top_neighbors": [n[0]["id"] for n in neighbors],
        "top_similarities": [n[1] for n in neighbors]
    }
