import json
import os
import pandas as pd
import numpy as np
import networkx as nx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Define paths relative to the script location
script_dir = os.path.dirname(os.path.abspath(__file__))
dataset_dir = os.path.join(script_dir, "dataset")
cluster_summary_path = os.path.join(script_dir, "..", "d1", "results", "cluster_summary.json")
services_path = os.path.join(dataset_dir, "services.json")
alerts_path = os.path.join(dataset_dir, "alerts_sample.jsonl")
history_path = os.path.join(dataset_dir, "incidents_history.json")
output_path = os.path.join(script_dir, "results", "rca_output.json")

# Load cluster summary
with open(cluster_summary_path, encoding='utf-8') as f:
    cluster_summary = json.load(f)

# Load services metadata
with open(services_path, encoding='utf-8') as f:
    services_data = json.load(f)

# Load historical incidents
with open(history_path, encoding='utf-8') as f:
    history_data = json.load(f)

# Load raw alerts
all_alerts = []
with open(alerts_path, encoding='utf-8') as f:
    for line in f:
        if line.strip():
            all_alerts.append(json.loads(line))

print(f"Successfully loaded:")
print(f"  - {len(cluster_summary['clusters'])} alert clusters from Day 1")
print(f"  - {len(services_data['services'])} services and {len(services_data['stores'])} stores from topology")
print(f"  - {len(all_alerts)} raw alerts")
print(f"  - {len(history_data['incidents'])} historical incidents")

# Build service graph
G = nx.DiGraph()
for svc in services_data['services']:
    G.add_node(svc['name'], type='service', criticality=svc.get('criticality', 'medium'))
for store in services_data['stores']:
    G.add_node(store['name'], type='store', criticality=store.get('criticality', 'medium'))
for edge in services_data['edges']:
    G.add_edge(edge['from'], edge['to'], type=edge.get('type', 'http'))

stores = services_data['stores']

def get_alerts_for_cluster(cluster, alerts):
    """Filter raw alerts that belong to a cluster by matching fingerprint, service, and time range."""
    cluster_services = set(cluster["services"])
    cluster_fingerprints = set(cluster["fingerprints"])
    t_start = pd.to_datetime(cluster["time_range"][0])
    t_end = pd.to_datetime(cluster["time_range"][1])
    
    matched = []
    for a in alerts:
        a_fp = f"{a['service']}|{a['metric']}|{a['severity']}"
        a_ts = pd.to_datetime(a["ts"])
        if a["service"] in cluster_services and a_fp in cluster_fingerprints and t_start <= a_ts <= t_end:
            matched.append(a)
    return matched

print(f"Service graph built with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")

results = []
clusters = cluster_summary["clusters"]
# Filter clusters with multiple services (requires actual root cause analysis)
multi_service_clusters = [c for c in clusters if len(c["services"]) > 1]

# 1. Prepare TF-IDF vectorizer for incident retrieval
incident_list = history_data["incidents"]
documents = []
for inc in incident_list:
    # Combine services involved, root cause service, and summary to build context document
    svcs = " ".join(inc["services_involved"])
    doc = f"{svcs} {inc['root_cause_service']} {inc['summary']}"
    documents.append(doc)

vectorizer = TfidfVectorizer(stop_words='english')
tfidf_matrix = vectorizer.fit_transform(documents)

# Helper to map alert severity to historical incident severity
def severity_matches(cluster_sev, incident_sev):
    if cluster_sev == "crit" and incident_sev == "critical":
        return True
    if cluster_sev == "warn" and incident_sev in ["high", "medium", "low"]:
        return True
    return False

# Process each cluster
for cluster in multi_service_clusters:
    c_id = cluster["cluster_id"]
    services = cluster["services"]
    max_severity = cluster["max_severity"]
    
    # Filter alerts matching this cluster
    c_alerts = get_alerts_for_cluster(cluster, all_alerts)
    
    # A. PageRank on reversed subgraph
    subg = G.subgraph(services)
    if len(services) > 1 and subg.number_of_edges() > 0:
        pagerank_scores = nx.pagerank(subg.reverse(copy=True), alpha=0.85)
    else:
        pagerank_scores = {node: 1.0 / len(services) for node in services}
        
    max_pr = max(pagerank_scores.values()) if pagerank_scores else 1.0
    pagerank_norm = {node: (score / max_pr if max_pr > 0 else 1.0) for node, score in pagerank_scores.items()}
    
    # B. Temporal Scorer based on earliest alert timestamp
    earliest_ts = {}
    for s in services:
        s_alerts = [a for a in c_alerts if a["service"] == s]
        if s_alerts:
            earliest_ts[s] = min(pd.to_datetime(a["ts"]) for a in s_alerts)
        else:
            earliest_ts[s] = pd.to_datetime(cluster["time_range"][0])
            
    ts_values = list(earliest_ts.values())
    t_min = min(ts_values)
    t_max = max(ts_values)
    
    timestamp_score = {}
    if t_max == t_min:
        for s in services:
            timestamp_score[s] = 1.0
    else:
        range_sec = (t_max - t_min).total_seconds()
        for s in services:
            ts = earliest_ts[s]
            diff_sec = (ts - t_min).total_seconds()
            timestamp_score[s] = 1.0 - (diff_sec / range_sec if range_sec > 0 else 0)
            
    # C. Combined score (0.6 * PageRank + 0.4 * Timestamp)
    combined_score = {}
    for s in services:
        combined_score[s] = 0.6 * pagerank_norm[s] + 0.4 * timestamp_score[s]
        
    # Sort candidates
    sorted_candidates = sorted(combined_score.items(), key=lambda x: x[1], reverse=True)
    
    # D. Terminal noise adjustment: nếu top candidate là DB/cache (store),
    #    kiểm tra xem nó alert trước hay sau app caller.
    #    Nếu app caller alert TRƯỚC → app là culprit, không phải DB.
    top_candidate, top_score = sorted_candidates[0]
    is_store = any(st["name"] == top_candidate for st in stores)
    if is_store:
        callers = [edge["from"] for edge in services_data["edges"] if edge["to"] == top_candidate and edge["from"] in services]
        if callers:
            t_store = earliest_ts[top_candidate]
            t_callers = [earliest_ts[c] for c in callers if c in earliest_ts]
            if t_callers:
                t_caller_min = min(t_callers)
                if t_store > t_caller_min:
                    # Swap caller and store
                    earliest_caller = min(callers, key=lambda c: earliest_ts[c])
                    caller_idx = -1
                    for idx, (name, val) in enumerate(sorted_candidates):
                        if name == earliest_caller:
                            caller_idx = idx
                            break
                    if caller_idx != -1:
                        temp = sorted_candidates.pop(caller_idx)
                        sorted_candidates.insert(0, (temp[0], top_score))
                        
    graph_top3 = sorted_candidates[:3]
    
    # E. TF-IDF Cosine Similarity Retrieval
    services_str = " ".join(services)
    fps = [fp.replace("|", " ").replace("_", " ") for fp in cluster["fingerprints"]]
    fps_str = " ".join(fps)
    query_text = f"{services_str} {fps_str}"
    
    query_vec = vectorizer.transform([query_text])
    sims = cosine_similarity(query_vec, tfidf_matrix).flatten()
    
    # Sort incidents by similarity score descending, tie-breaking by timestamp descending
    sorted_indices = []
    for idx, sim in enumerate(sims):
        if sim > 0:
            sorted_indices.append((idx, sim, pd.to_datetime(incident_list[idx]["ts"])))
    sorted_indices.sort(key=lambda x: (x[1], x[2]), reverse=True)
    
    top_incidents = []
    for idx, sim, _ in sorted_indices[:3]:
        top_incidents.append((incident_list[idx], sim))
        
    similar_incident_ids = [item[0]["id"] for item in top_incidents]
    
    # F. Classification with Fallback
    if not top_incidents:
        root_cause = graph_top3[0][0]
        root_class = "other"
        confidence = float(graph_top3[0][1])
        actions = ["Investigate manually"]
        reasoning = "No similar historical incidents found. Fallback to top graph candidate."
        method = "graph-only-fallback"
    else:
        best_inc, best_score = top_incidents[0]
        root_cause = best_inc["root_cause_service"]
        
        # Validation guard: root cause must be in services list
        if root_cause not in services:
            root_cause = graph_top3[0][0]
            
        root_class = best_inc["root_cause_class"]
        confidence = float(combined_score.get(root_cause, graph_top3[0][1]))
        
        # Split remediation into actions
        remediation_str = best_inc["remediation"]
        parts = remediation_str.split(". ")
        actions = []
        for p in parts:
            p = p.strip()
            if p:
                if p.endswith('.'):
                    p = p[:-1].strip()
                if p:
                    actions.append(p)
                    
        reasoning = f"Matched historical incident {best_inc['id']} with TF-IDF similarity {best_score:.4f}."
        method = "graph+retrieval"
        
    # Append results
    results.append({
        "cluster_id": c_id,
        "graph_top3": [[name, round(float(val), 3)] for name, val in graph_top3],
        "root_cause": root_cause,
        "class": root_class,
        "confidence": round(confidence, 3),
        "actions": actions,
        "reasoning": reasoning,
        "similar_incidents": similar_incident_ids,
        "method": method
    })

# Ensure results folder exists
os.makedirs(os.path.dirname(output_path), exist_ok=True)
# Save to output file
output_data = {
    "clusters_analyzed": len(multi_service_clusters),
    "results": results
}
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(output_data, f, indent=2, ensure_ascii=False)

print(f"Analysis complete. Saved results for {len(multi_service_clusters)} clusters to {output_path}")
