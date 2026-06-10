"""
serve.py — FastAPI entry-point cho AIOps incident pipeline.

Architecture
────────────
Module-level singletons (loaded once on import):
    GRAPH   : nx.DiGraph  — service topology
    HISTORY : dict        — historical incidents

Request flow (POST /incident):
    IncidentRequest(alerts: list[Alert])
        → model_dump()
        → process_batch(alerts)
            → correlate()    Layer 1   alert dedup + clustering
            → run_rca()      Layer 2   PageRank + TF-IDF RCA
        → IncidentResponse

Feature flags (env vars):
    AIOPS_USE_LLM=false  →  skip LLM call, use graph-only output
"""

import json
import logging
import os
import time

import networkx as nx
from datetime import datetime, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from prometheus_client import Counter, Histogram, make_asgi_app

from correlate import correlate
from run_rca import run_rca

# ─────────────────────────────────────────────────────────────────────────────
# JSON Logging Formatter (§9.3)
# ─────────────────────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts":     datetime.now(timezone.utc).isoformat(),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        # Extra fields passed via extra={"extra": {...}}
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            payload.update(record.extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logging.root.handlers = [_handler]
logging.root.setLevel(logging.INFO)

log = logging.getLogger("aiops.serve")

# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────────────────────────────────────

load_dotenv()

# Feature flag: set AIOPS_USE_LLM=false to bypass LLM call entirely (kill-switch)
_USE_LLM: bool = os.getenv("AIOPS_USE_LLM", "true").lower() not in ("false", "0", "no")

# ─────────────────────────────────────────────────────────────────────────────
# Module-level singletons — loaded ONCE per worker process (§4 + §3.3)
# ─────────────────────────────────────────────────────────────────────────────

_DATASET_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")
_SERVICES_PATH = os.path.join(_DATASET_DIR, "services.json")
_HISTORY_PATH  = os.path.join(_DATASET_DIR, "incidents_history.json")


def _build_graph(services_path: str) -> nx.DiGraph:
    with open(services_path, encoding="utf-8") as f:
        data = json.load(f)
    G = nx.DiGraph()
    for svc in data["services"]:
        G.add_node(svc["name"], type="service",
                   criticality=svc.get("criticality", "medium"))
    for store in data["stores"]:
        G.add_node(store["name"], type="store",
                   criticality=store.get("criticality", "medium"))
    for edge in data["edges"]:
        G.add_edge(edge["from"], edge["to"],
                   type=edge.get("type", "http"))
    log.info("Graph loaded", extra={"extra": {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "source": services_path,
    }})
    return G


def _load_history(history_path: str) -> dict:
    with open(history_path, encoding="utf-8") as f:
        data = json.load(f)
    log.info("History loaded", extra={"extra": {
        "incident_count": len(data.get("incidents", [])),
    }})
    return data


# Cached singletons
GRAPH   = _build_graph(_SERVICES_PATH)
HISTORY = _load_history(_HISTORY_PATH)

_GRAPH_META: dict = {
    "app":              "1.0.0",
    "graph_version":    f"g-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
    "graph_loaded_at":  datetime.now(timezone.utc).isoformat(),
    "graph_source":     "services.json",
    "graph_node_count": GRAPH.number_of_nodes(),
    "graph_edge_count": GRAPH.number_of_edges(),
    "pipeline_config": {
        "gap_sec":    120,
        "max_hop":    2,
        "rca_method": "pagerank+tfidf",
        "llm_enabled": _USE_LLM,
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Prometheus metrics (§9.1)
# ─────────────────────────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "aiops_incident_requests_total",
    "Total /incident requests",
    ["status"],              # success | error
)

REQUEST_LATENCY = Histogram(
    "aiops_incident_latency_seconds",
    "End-to-end latency of /incident endpoint",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

LLM_FAILURES = Counter(
    "aiops_llm_failures_total",
    "LLM call failures",
    ["reason"],              # timeout | 5xx | parse_error
)

CLUSTERS_PER_REQUEST = Histogram(
    "aiops_clusters_per_request",
    "Number of alert clusters produced per request",
    buckets=[1, 2, 3, 5, 10, 20],
)

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas (§2.1 + §2.2)
# ─────────────────────────────────────────────────────────────────────────────


class Alert(BaseModel):
    id:        str
    ts:        str
    service:   str
    metric:    str
    severity:  str
    value:     float
    threshold: float
    labels:    dict[str, str] = {}


class IncidentRequest(BaseModel):
    alerts: list[Alert] = Field(..., min_length=1,
                                description="Batch of raw alerts (≥ 1 required)")

    @field_validator("alerts")
    @classmethod
    def alerts_not_empty(cls, v: list) -> list:
        if len(v) == 0:
            raise ValueError("alerts list must not be empty")
        return v


class IncidentResponse(BaseModel):
    clusters:            list[dict]
    root_cause:          str
    confidence:          float = 0.0
    root_cause_class:    str   = "unknown"
    recommended_actions: list[str]
    similar_incidents:   list[str]
    reasoning:           str   = ""
    method:              str   = "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline — Glue layer (§4)
# ─────────────────────────────────────────────────────────────────────────────


def process_batch(alerts: list[dict]) -> IncidentResponse:
    """
    Chain Layer-1 (correlate) → Layer-2 (run_rca).

    Parameters
    ----------
    alerts : plain Python dicts — model_dump() output from IncidentRequest.

    Returns
    -------
    IncidentResponse  (never raises; caller wraps in try/except)
    """
    # ── Layer 1: alert dedup + clustering ────────────────────────────────────
    summary  = correlate(alerts, GRAPH, gap_sec=120, max_hop=2)
    clusters = summary.get("clusters", [])

    CLUSTERS_PER_REQUEST.observe(len(clusters))

    if not clusters:
        log.warning("No clusters produced — returning early")
        return IncidentResponse(
            clusters=[],
            root_cause="unknown",
            recommended_actions=[],
            similar_incidents=[],
            reasoning="Alert correlator produced no clusters.",
            method="no-clusters",
        )

    # ── Pick largest cluster as primary incident ──────────────────────────────
    primary = max(clusters, key=lambda c: c["alert_count"])
    log.info("Primary cluster selected", extra={"extra": {
        "cluster_id":   primary["cluster_id"],
        "alert_count":  primary["alert_count"],
        "services":     primary["services"],
    }})

    # ── Layer 2: RCA ─────────────────────────────────────────────────────────
    if not _USE_LLM:
        # Kill-switch: feature flag off → graph-only output, skip LLM
        log.info("LLM disabled via AIOPS_USE_LLM flag — using graph-only RCA")
        rca = run_rca(primary, alerts, GRAPH, HISTORY)
        rca["method"] = "graph-only-flag-off"
    else:
        rca = run_rca(primary, alerts, GRAPH, HISTORY)

    # ── Pack into IncidentResponse ────────────────────────────────────────────
    return IncidentResponse(
        clusters=clusters,
        root_cause=rca["root_cause"],
        confidence=rca["confidence"],
        root_cause_class=rca.get("class", "unknown"),
        recommended_actions=rca.get("actions", []),
        similar_incidents=rca.get("similar_incidents", []),
        reasoning=rca.get("reasoning", ""),
        method=rca.get("method", "unknown"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AIOps Incident Pipeline",
    description="Alert correlation + RCA as a service",
    version=_GRAPH_META["app"],
)

# Mount Prometheus /metrics endpoint (§9.1)
app.mount("/metrics", make_asgi_app())


# ── Latency middleware (§5.1) ─────────────────────────────────────────────────

@app.middleware("http")
async def latency_middleware(request: Request, call_next):
    """
    Measure wall-clock latency per request.
    - Adds X-Response-Time-Ms response header.
    - Emits a structured JSON log line: {method, path, status, duration_ms}.
    """
    t0 = time.perf_counter()
    response: Response = await call_next(request)
    duration_ms = round((time.perf_counter() - t0) * 1000, 2)

    response.headers["X-Response-Time-Ms"] = str(duration_ms)

    log.info("HTTP request", extra={"extra": {
        "method":      request.method,
        "path":        request.url.path,
        "status":      response.status_code,
        "duration_ms": duration_ms,
    }})
    return response


# ── Health probes (§7) ────────────────────────────────────────────────────────

@app.get("/healthz", tags=["ops"], summary="Liveness probe")
async def healthz():
    """Process-alive check — always 200 if uvicorn is up."""
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"], summary="Readiness probe")
async def readyz():
    """
    Dependency-ready check.

    Returns 503 if graph or incident history failed to load.
    LLM API availability is intentionally NOT checked here:
    if OpenAI is down, we still serve traffic via graph-only fallback.
    """
    graph_ok   = GRAPH is not None and GRAPH.number_of_nodes() > 0
    history_ok = HISTORY is not None and len(HISTORY.get("incidents", [])) > 0

    checks = {
        "graph":   "OK" if graph_ok   else "FAIL",
        "history": "OK" if history_ok else "FAIL",
    }
    if not graph_ok or not history_ok:
        raise HTTPException(status_code=503, detail=checks)
    return {"status": "ok", "checks": checks}


@app.get("/version", tags=["ops"], summary="Version + pipeline config")
async def get_version():
    """
    Graph snapshot metadata + pipeline configuration.

    Useful for debugging correlation regressions: check graph_version
    before blaming code.
    """
    return _GRAPH_META


# ── Main endpoint (§2.1 + §4) ────────────────────────────────────────────────

@app.post(
    "/incident",
    response_model=IncidentResponse,
    tags=["pipeline"],
    summary="Run full AIOps pipeline on a batch of alerts",
)
async def analyze_incident(body: IncidentRequest):
    """
    Receive a batch of raw alerts, run correlation + RCA, return incident report.

    - Empty `alerts` list → **422** (Pydantic validation)
    - Pipeline error      → **500** with short message (stack trace stays server-side)
    """
    # Convert Pydantic models → plain dicts (pipeline is framework-agnostic)
    alert_dicts: list[dict] = [a.model_dump() for a in body.alerts]

    t0 = time.perf_counter()
    try:
        result = process_batch(alert_dicts)
        REQUEST_COUNT.labels(status="success").inc()
    except Exception:
        REQUEST_COUNT.labels(status="error").inc()
        log.error(
            "process_batch failed",
            exc_info=True,
            extra={"extra": {"alert_count": len(alert_dicts)}},
        )
        raise HTTPException(
            status_code=500,
            detail="Internal pipeline error — check server logs for details.",
        )
    finally:
        REQUEST_LATENCY.observe(time.perf_counter() - t0)

    log.info("Incident processed", extra={"extra": {
        "root_cause":    result.root_cause,
        "confidence":    result.confidence,
        "cluster_count": len(result.clusters),
        "method":        result.method,
    }})
    return result
