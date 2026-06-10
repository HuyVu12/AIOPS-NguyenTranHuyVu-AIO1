"""
tests/test_serve.py — Unit + Integration tests cho AIOps pipeline.

§10.1 Unit test — pure functions (fingerprint, session_groups)
§10.2 Integration test — TestClient endpoint
§10.3 Mock LLM trong test (không gọi LLM thật)
"""
import json
import sys
import os
import pytest

# Đảm bảo import từ thư mục cha (d3/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ─────────────────────────────────────────────────────────────────────────────
# §10.1 Unit tests — pure functions
# ─────────────────────────────────────────────────────────────────────────────

from correlate import fingerprint, session_groups


def test_fingerprint_excludes_timestamp_and_value():
    """
    fingerprint() phải EXCLUDE timestamp và value —
    2 alert chỉ khác ts và value phải có cùng fingerprint.
    """
    a1 = {"id": "a1", "ts": "2026-06-12T09:42:01Z",
          "service": "payment-svc", "metric": "db_pool", "severity": "warn", "value": 0.85}
    a2 = {"id": "a2", "ts": "2026-06-12T09:43:00Z",   # ts khác
          "service": "payment-svc", "metric": "db_pool", "severity": "warn", "value": 0.99}  # value khác

    assert fingerprint(a1) == fingerprint(a2)
    assert fingerprint(a1) == "payment-svc|db_pool|warn"


def test_fingerprint_differs_on_service():
    a1 = {"service": "payment-svc", "metric": "db_pool", "severity": "warn"}
    a2 = {"service": "checkout-svc", "metric": "db_pool", "severity": "warn"}
    assert fingerprint(a1) != fingerprint(a2)


def test_session_groups_gap_within_threshold():
    """Alerts trong cùng gap_sec phải được gom vào 1 session."""
    alerts = [
        {"id": "a1", "ts": "2026-06-12T09:42:00Z", "service": "svc-a",
         "metric": "m", "severity": "warn", "value": 1.0},
        {"id": "a2", "ts": "2026-06-12T09:43:00Z", "service": "svc-b",
         "metric": "m", "severity": "warn", "value": 1.0},  # 60s gap
    ]
    groups = session_groups(alerts, gap_sec=120)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_session_groups_gap_exceeds_threshold():
    """Alerts cách nhau > gap_sec phải nằm ở 2 session khác nhau."""
    alerts = [
        {"id": "a1", "ts": "2026-06-12T09:42:00Z", "service": "svc-a",
         "metric": "m", "severity": "warn", "value": 1.0},
        {"id": "a2", "ts": "2026-06-12T09:46:01Z", "service": "svc-b",
         "metric": "m", "severity": "warn", "value": 1.0},  # 241s gap > 120s
    ]
    groups = session_groups(alerts, gap_sec=120)
    assert len(groups) == 2


# ─────────────────────────────────────────────────────────────────────────────
# §10.2 Integration tests — endpoint via TestClient
# ─────────────────────────────────────────────────────────────────────────────

from fastapi.testclient import TestClient
from serve import app

client = TestClient(app)


# ── Fixtures ──────────────────────────────────────────────────────────────────

VALID_ALERT = {
    "id":        "a-0001",
    "ts":        "2026-06-12T09:42:01Z",
    "service":   "payment-svc",
    "metric":    "db_connection_pool_used_ratio",
    "severity":  "warn",
    "value":     0.85,
    "threshold": 0.80,
    "labels":    {},
}


# ── Liveness probe ────────────────────────────────────────────────────────────

def test_healthz_returns_200():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── Readiness probe ───────────────────────────────────────────────────────────

def test_readyz_returns_200_when_data_loaded():
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── Version endpoint ──────────────────────────────────────────────────────────

def test_version_contains_required_fields():
    r = client.get("/version")
    assert r.status_code == 200
    data = r.json()
    assert "graph_version" in data
    assert "graph_node_count" in data
    assert data["graph_node_count"] > 0


# ── Main endpoint: empty alerts → 422 (Pydantic validation) ──────────────────

def test_incident_empty_alerts_returns_422():
    """Empty alerts list phải trả 422, không phải 500."""
    r = client.post("/incident", json={"alerts": []})
    assert r.status_code == 422, f"Expected 422, got {r.status_code}"


def test_incident_missing_alerts_field_returns_422():
    """Thiếu field alerts phải trả 422."""
    r = client.post("/incident", json={})
    assert r.status_code == 422


def test_incident_wrong_type_returns_422():
    """alerts phải là list, nếu là string → 422."""
    r = client.post("/incident", json={"alerts": "not-a-list"})
    assert r.status_code == 422


def test_incident_missing_required_field_returns_422():
    """Alert thiếu field bắt buộc (vd: service) → 422."""
    bad_alert = {k: v for k, v in VALID_ALERT.items() if k != "service"}
    r = client.post("/incident", json={"alerts": [bad_alert]})
    assert r.status_code == 422


# ── Main endpoint: valid input → 200 với đúng schema ─────────────────────────

def test_incident_valid_input_returns_200():
    r = client.post("/incident", json={"alerts": [VALID_ALERT]})
    assert r.status_code == 200, f"Got {r.status_code}: {r.text}"


def test_incident_response_has_required_fields():
    """Response phải có clusters, root_cause, recommended_actions."""
    r = client.post("/incident", json={"alerts": [VALID_ALERT]})
    assert r.status_code == 200
    data = r.json()

    assert "clusters"            in data, "Missing 'clusters'"
    assert "root_cause"          in data, "Missing 'root_cause'"
    assert "recommended_actions" in data, "Missing 'recommended_actions'"
    assert "similar_incidents"   in data, "Missing 'similar_incidents'"
    assert "confidence"          in data, "Missing 'confidence'"


def test_incident_clusters_is_list():
    r = client.post("/incident", json={"alerts": [VALID_ALERT]})
    assert r.status_code == 200
    assert isinstance(r.json()["clusters"], list)


def test_incident_response_time_header_present():
    """Latency middleware phải đính X-Response-Time-Ms vào mọi response."""
    r = client.post("/incident", json={"alerts": [VALID_ALERT]})
    assert "x-response-time-ms" in r.headers or "X-Response-Time-Ms" in r.headers


def test_incident_multiple_alerts_batch():
    """Pipeline phải xử lý được batch nhiều alerts."""
    alerts = [
        {**VALID_ALERT, "id": f"a-{i:04d}",
         "ts": f"2026-06-12T09:42:{i:02d}Z",
         "value": 0.80 + i * 0.01}
        for i in range(5)
    ]
    r = client.post("/incident", json={"alerts": alerts})
    assert r.status_code == 200
    data = r.json()
    assert data["root_cause"] != ""
