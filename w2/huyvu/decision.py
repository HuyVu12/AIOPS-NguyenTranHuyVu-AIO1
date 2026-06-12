def get_action_meta(action_name: str, actions_catalog: list[dict]) -> dict:
    """Find cost and blast radius metadata for a given action name in actions_catalog."""
    for act in actions_catalog:
        if act["name"] == action_name:
            return act
    # Default fallback metadata
    return {
        "name": action_name,
        "cost_min": 10,
        "downtime_min": 5,
        "blast_radius_services": 1,
        "rollback_window_sec": 60
    }

def select_action(retrieval_output: dict, actions_catalog: list[dict]) -> dict:
    """Choose the best action using Expected Utility (EV) maximization with Blast Radius Gates."""
    candidates = retrieval_output.get("candidates", [])
    is_ood = retrieval_output.get("is_ood", False)
    max_similarity = retrieval_output.get("max_similarity", 0.0)
    
    # 1. Escalate immediately if the incident is Out-of-Distribution (OOD)
    if is_ood:
        return {
            "selected_action": "page_oncall",
            "params": {"team": "platform-team"},
            "confidence": round(1.0 - max_similarity, 2) if max_similarity < 1.0 else 0.1,
            "evidence": {
                "reasoning": f"Out-of-Distribution incident. Max similarity ({max_similarity:.2f}) < 0.35 threshold. Escalating to on-call.",
                "max_similarity": max_similarity
            }
        }
        
    scored_candidates = []
    for c in candidates:
        name = c["name"]
        params = c["params"]
        vote_score = c.get("voting_score", 0.0)
        
        if vote_score <= 0:
            continue
            
        prob = max(0.1, min(0.9, vote_score))
        
        meta = get_action_meta(name, actions_catalog)
        cost_min = meta.get("cost_min", 10)
        downtime_min = meta.get("downtime_min", 2)
        blast_radius = meta.get("blast_radius_services", 1)
        rollback_sec = meta.get("rollback_window_sec", 60)
        
        # Action Cost Formula: cost_min + downtime_min * 2 + blast_radius * 5
        action_cost = cost_min + 2 * downtime_min + 5 * blast_radius
        
        # Utility values
        utility_success = 100 - action_cost
        utility_failure = -50 - action_cost - 0.5 * rollback_sec
        
        # Expected Value (EV) calculation
        ev = prob * utility_success + (1 - prob) * utility_failure
        
        scored_candidates.append({
            "name": name,
            "params": params,
            "confidence": prob,
            "ev": ev,
            "blast_radius": blast_radius
        })
        
    # Standard utility score representing human callout cost for page_oncall
    ev_page_oncall = 25.0
    
    best_candidate = None
    if scored_candidates:
        scored_candidates.sort(key=lambda x: x["ev"], reverse=True)
        best_candidate = scored_candidates[0]
        
    # 2. If no candidate exists or the highest candidate's EV is less than page_oncall, page oncall
    if not best_candidate or best_candidate["ev"] <= ev_page_oncall:
        return {
            "selected_action": "page_oncall",
            "params": {"team": "platform-team"},
            "confidence": round(1.0 - max_similarity, 2) if max_similarity > 0 else 0.5,
            "evidence": {
                "reasoning": "No candidate action has expected utility above page_oncall utility threshold. Escalating.",
                "evaluated_candidates": scored_candidates
            }
        }
        
    # 3. Blast Radius Gate (protect against auto-acting with low confidence on large impact actions)
    if best_candidate["blast_radius"] >= 3 and best_candidate["confidence"] < 0.65:
        return {
            "selected_action": "page_oncall",
            "params": {"team": "platform-team"},
            "confidence": round(best_candidate["confidence"], 2),
            "evidence": {
                "reasoning": f"Action '{best_candidate['name']}' has high blast radius ({best_candidate['blast_radius']}) but low confidence ({best_candidate['confidence']:.2f}). Escaping to on-call.",
                "original_candidate": best_candidate
            }
        }
        
    return {
        "selected_action": best_candidate["name"],
        "params": best_candidate["params"],
        "confidence": round(best_candidate["confidence"], 2),
        "evidence": {
            "reasoning": f"Action selected via Expected Utility maximization (EV: {best_candidate['ev']:.2f}).",
            "ev": round(best_candidate["ev"], 2),
            "similarity_score": round(max_similarity, 2)
        }
    }
