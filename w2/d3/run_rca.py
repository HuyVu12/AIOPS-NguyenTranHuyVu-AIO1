import json
import os
import pandas as pd
import numpy as np
import networkx as nx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


# ─────────────────────────────────────────────────────────────────────────────
# Helper: filter raw alerts that belong to a cluster
# ─────────────────────────────────────────────────────────────────────────────

def get_alerts_for_cluster(cluster: dict, alerts: list[dict]) -> list[dict]:
    """Return raw alerts whose (service, fingerprint, timestamp) match the cluster."""
    cluster_services     = set(cluster["services"])
    cluster_fingerprints = set(cluster["fingerprints"])
    t_start = pd.to_datetime(cluster["time_range"][0])
    t_end   = pd.to_datetime(cluster["time_range"][1])

    matched = []
    for a in alerts:
        a_fp = f"{a['service']}|{a['metric']}|{a['severity']}"
        a_ts = pd.to_datetime(a["ts"])
        if (
            a["service"] in cluster_services
            and a_fp in cluster_fingerprints
            and t_start <= a_ts <= t_end
        ):
            matched.append(a)
    return matched


# ─────────────────────────────────────────────────────────────────────────────
# Core RCA function (Layer 2)
# ─────────────────────────────────────────────────────────────────────────────

def run_rca(
    cluster:  dict,
    alerts:   list[dict],
    graph:    nx.DiGraph,
    history:  dict,
) -> dict:
    """
    Perform Root Cause Analysis for a single alert cluster.

    Parameters
    ----------
    cluster  : cluster dict produced by correlate() — must contain
               'cluster_id', 'services', 'fingerprints', 'time_range', 'max_severity'.
    alerts   : list of raw alert dicts (full dataset, not pre-filtered).
    graph    : nx.DiGraph of the service topology (nodes = services/stores).
    history  : dict with key 'incidents' → list of historical incident dicts.

    Returns
    -------
    dict with keys:
        cluster_id, graph_top3, root_cause, class, confidence,
        actions, reasoning, similar_incidents, method
    """
    services      = cluster["services"]
    c_id          = cluster["cluster_id"]
    incident_list = history["incidents"]

    # Identify store nodes from graph metadata
    stores = [
        n for n, d in graph.nodes(data=True)
        if d.get("type") == "store"
    ]

    # ── A. Filter alerts for this cluster ────────────────────────────────────
    c_alerts = get_alerts_for_cluster(cluster, alerts)

    # ── B. PageRank on reversed subgraph ─────────────────────────────────────
    subg = graph.subgraph(services)
    if len(services) > 1 and subg.number_of_edges() > 0:
        pagerank_scores = nx.pagerank(subg.reverse(copy=True), alpha=0.85)
    else:
        pagerank_scores = {node: 1.0 / len(services) for node in services}

    max_pr = max(pagerank_scores.values()) if pagerank_scores else 1.0
    pagerank_norm = {
        node: (score / max_pr if max_pr > 0 else 1.0)
        for node, score in pagerank_scores.items()
    }

    # ── C. Temporal score (earliest alert timestamp per service) ──────────────
    earliest_ts: dict = {}
    for s in services:
        s_alerts = [a for a in c_alerts if a["service"] == s]
        if s_alerts:
            earliest_ts[s] = min(pd.to_datetime(a["ts"]) for a in s_alerts)
        else:
            earliest_ts[s] = pd.to_datetime(cluster["time_range"][0])

    t_min = min(earliest_ts.values())
    t_max = max(earliest_ts.values())
    range_sec = (t_max - t_min).total_seconds()

    timestamp_score: dict = {}
    for s in services:
        if range_sec == 0:
            timestamp_score[s] = 1.0
        else:
            diff_sec = (earliest_ts[s] - t_min).total_seconds()
            timestamp_score[s] = 1.0 - (diff_sec / range_sec)

    # ── D. Combined score (0.6 × PageRank + 0.4 × Temporal) ─────────────────
    combined_score = {
        s: 0.6 * pagerank_norm[s] + 0.4 * timestamp_score[s]
        for s in services
    }
    sorted_candidates = sorted(
        combined_score.items(), key=lambda x: x[1], reverse=True
    )

    # ── E. Terminal noise check (if top candidate is a DB/cache store) ────────
    top_candidate, top_score = sorted_candidates[0]
    if top_candidate in stores:
        # Find callers of the store that are also in this cluster
        callers = [
            u for u, v in graph.edges()
            if v == top_candidate and u in services
        ]
        if callers:
            t_store      = earliest_ts[top_candidate]
            t_caller_min = min(earliest_ts[c] for c in callers if c in earliest_ts)
            if t_store > t_caller_min:
                # App caller alerted before DB → app is the real culprit
                earliest_caller = min(callers, key=lambda c: earliest_ts[c])
                caller_idx = next(
                    (i for i, (n, _) in enumerate(sorted_candidates)
                     if n == earliest_caller),
                    -1,
                )
                if caller_idx != -1:
                    temp = sorted_candidates.pop(caller_idx)
                    sorted_candidates.insert(0, (temp[0], top_score))

    graph_top3 = sorted_candidates[:3]

    # ── F. TF-IDF cosine-similarity retrieval over incident history ───────────
    documents = []
    for inc in incident_list:
        svcs = " ".join(inc["services_involved"])
        doc  = f"{svcs} {inc['root_cause_service']} {inc['summary']}"
        documents.append(doc)

    vectorizer   = TfidfVectorizer(stop_words="english")
    tfidf_matrix = vectorizer.fit_transform(documents)

    services_str = " ".join(services)
    fps_str      = " ".join(
        fp.replace("|", " ").replace("_", " ")
        for fp in cluster["fingerprints"]
    )
    query_text = f"{services_str} {fps_str}"
    query_vec  = vectorizer.transform([query_text])
    sims       = cosine_similarity(query_vec, tfidf_matrix).flatten()

    sorted_indices = sorted(
        [(idx, sim, pd.to_datetime(incident_list[idx]["ts"]))
         for idx, sim in enumerate(sims) if sim > 0],
        key=lambda x: (x[1], x[2]),
        reverse=True,
    )
    top_incidents = [
        (incident_list[idx], sim)
        for idx, sim, _ in sorted_indices[:3]
    ]
    similar_incident_ids = [item[0]["id"] for item in top_incidents]

    # ── G. Classification with fallback ──────────────────────────────────────
    if not top_incidents:
        root_cause = graph_top3[0][0]
        root_class = "other"
        confidence = float(graph_top3[0][1])
        actions    = ["Investigate manually"]
        reasoning  = "No similar historical incidents found. Fallback to top graph candidate."
        method     = "graph-only-fallback"
    else:
        best_inc, best_score = top_incidents[0]
        root_cause = best_inc["root_cause_service"]

        # Guard: root cause must belong to this cluster's services
        if root_cause not in services:
            root_cause = graph_top3[0][0]

        root_class = best_inc["root_cause_class"]
        confidence = float(combined_score.get(root_cause, graph_top3[0][1]))

        remediation_str = best_inc["remediation"]
        actions = [
            p.strip().rstrip(".")
            for p in remediation_str.split(". ")
            if p.strip()
        ]
        reasoning = (
            f"Matched historical incident {best_inc['id']} "
            f"with TF-IDF similarity {best_score:.4f}."
        )
        method = "graph+retrieval"

    return {
        "cluster_id":        c_id,
        "graph_top3":        [[name, round(float(val), 3)] for name, val in graph_top3],
        "root_cause":        root_cause,
        "class":             root_class,
        "confidence":        round(confidence, 3),
        "actions":           actions,
        "reasoning":         reasoning,
        "similar_incidents": similar_incident_ids,
        "method":            method,
    }
