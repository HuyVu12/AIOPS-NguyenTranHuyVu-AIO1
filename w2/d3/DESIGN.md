# DESIGN.md — AIOps Incident Pipeline (W2-D3)

## 1. Pipeline Architecture

```
POST /incident
        │
        │  IncidentRequest { alerts: list[Alert] }
        ▼
┌─────────────────────────────────────────────────────┐
│                  serve.py                           │
│                                                     │
│  Pydantic validation (422 on bad input)             │
│  ──────────────────────────────────────             │
│  model_dump() → plain dicts                         │
│         │                                           │
│         ▼                                           │
│  process_batch(alerts)                              │
│  ┌──────────────────────────────────────────────┐   │
│  │  Layer 1 — correlate()                       │   │
│  │  · Deduplication (fingerprint-based)         │   │
│  │  · Session windowing (gap_sec=120s)          │   │
│  │  · Topology grouping (max_hop=2, Union-Find) │   │
│  │  → list[cluster]                             │   │
│  │                                              │   │
│  │  Pick largest cluster (max alert_count)      │   │
│  │         │                                    │   │
│  │  Layer 2 — run_rca()                         │   │
│  │  · PageRank on reversed subgraph (α=0.85)    │   │
│  │  · Temporal score (earliest alert = 1.0)     │   │
│  │  · Combined = 0.6×PageRank + 0.4×Temporal    │   │
│  │  · Terminal noise check (DB vs app caller)   │   │
│  │  · TF-IDF cosine retrieval over 29 incidents │   │
│  │  → {root_cause, confidence, actions, ...}    │   │
│  └──────────────────────────────────────────────┘   │
│         │                                           │
│         ▼                                           │
│  IncidentResponse (JSON)                            │
└─────────────────────────────────────────────────────┘
```

**Module-level singletons** — `GRAPH` (networkx.DiGraph) và `HISTORY` (list[dict])
được load **một lần khi import**, cache suốt vòng đời process. Không reload mỗi
request → zero I/O cost per request cho hai assets này.

---

## 2. Latency Budget Breakdown

Đo trên dataset 20 alert thật (single-worker, no LLM):

| Phase | Thời gian ước tính | Scale behavior |
|---|---|---|
| Pydantic validate | ~0.5 ms | O(n alerts) — linear |
| `correlate()` — dedup + session | ~2 ms | O(n alerts) — linear |
| `correlate()` — topology grouping | ~3 ms | O(s²) với s = số services trong graph |
| `run_rca()` — PageRank | ~5 ms | O(E) với E = edges trong subgraph |
| `run_rca()` — TF-IDF retrieval | ~15 ms | O(d × v) với d = docs, v = vocab |
| JSON serialize | ~0.5 ms | O(clusters) |
| **Tổng (no LLM)** | **~26 ms** | |
| LLM call (nếu bật) | ~3–8 s | Fixed cost per call |

**Bottleneck thực tế**: TF-IDF vectorizer được build lại mỗi `run_rca()` call
vì function nhận `history` làm argument thay vì giữ state. Optimization rõ ràng
nhất: cache `(vectorizer, tfidf_matrix)` ở module-level trong `run_rca.py` và
invalidate khi `history` thay đổi. Trade-off: hiện tại chọn đơn giản hơn để
tránh stale cache bug.

**Phase nào scale linear với input × 10?**
- `correlate()` dedup + session: linear với số alert
- TF-IDF query transform: cố định (chỉ transform 1 query vector)
- PageRank: phụ thuộc subgraph size, không phải tổng alert count

---

## 3. Production Concern — Concurrency & Shared State

**Vấn đề**: Nhiều concurrent request cùng đọc `GRAPH` và `HISTORY`.

**Cách xử lý hiện tại**: Read-only shared state — `GRAPH` và `HISTORY` không bị
mutate sau khi load. Python GIL và immutable read pattern đảm bảo không có race
condition với nhiều async coroutine trong 1 worker.

**Giới hạn**: Với `--workers 4`, mỗi process có copy riêng của `GRAPH`/`HISTORY`.
In-memory cache (nếu có) không cross-worker. Giải pháp cho production thật:
- **Stateless worker**: dùng Redis để share cache
- **Reload mechanism**: background thread reload graph mỗi 5 phút, atomic swap
  `GRAPH = new_graph` (thread-safe vì assignment là atomic trong CPython)

**LLM down**: Feature flag `AIOPS_USE_LLM=false` cho phép disable LLM call ngay
lập tức bằng env var + restart pod — không cần redeploy code. `run_rca()` vẫn
chạy graph-only path với `method = "graph-only-flag-off"`. Endpoint không hang.

---

## 4. Framework Decision — FastAPI vs Flask vs BentoML

**Chọn FastAPI** vì:

| Tiêu chí | FastAPI | Flask | BentoML |
|---|---|---|---|
| Pydantic validation | Native, 0 code | Tự viết | Native |
| Async support | Native `async def` | Cần extension | Có |
| OpenAPI auto-docs | Có (`/docs`) | Không | Có |
| LLM IO-bound call | Hưởng lợi async | Blocking | Có |
| Overhead với non-ML workload | Thấp | Thấp | **Cao** |
| Learning curve | Thấp | Rất thấp | Cao |

Pipeline này **không phải ML-model-centric** (không có `.predict()` trên PyTorch
model), nên BentoML overhead không có lợi. Flask thiếu native async — LLM call
sẽ blocking. FastAPI là điểm cân bằng tốt nhất cho pipeline hỗn hợp (graph
algorithm + optional LLM call).

**Cụ thể về gap_sec=120s**: Chọn 2 phút vì cascade failure trong microservice
điển hình kết thúc trong 30–90 giây. 120 giây bao phủ được hầu hết cascade mà
không gom nhầm 2 incident riêng biệt cách nhau > 3 phút. Giá trị này được
document trong `/version` endpoint để dễ trace khi correlation regress.

---

## 5. Health Check Design

**`/healthz`** (liveness): chỉ return 200. Không check dependency. Kubernetes
dùng để biết process còn sống hay cần restart.

**`/readyz`** (readiness): check `GRAPH.number_of_nodes() > 0` và
`len(HISTORY["incidents"]) > 0`. Nếu fail → 503, pod bị remove khỏi load
balancer rotation.

**LLM API** không được check trong `/readyz` — nếu OpenAI down, chúng ta vẫn
muốn phục vụ traffic bằng graph-only path (`AIOPS_USE_LLM=false`). Check LLM
trong readyz sẽ làm toàn bộ pod mark not-ready khi provider outage — hành vi
không mong muốn.

---

## 6. Observability

**Prometheus metrics** (scrape tại `/metrics`):

| Metric | Type | Labels | SLO |
|---|---|---|---|
| `aiops_incident_requests_total` | Counter | `status` | error rate < 1% |
| `aiops_incident_latency_seconds` | Histogram | — | p99 < 10s |
| `aiops_llm_failures_total` | Counter | `reason` | < 1% of calls |
| `aiops_clusters_per_request` | Histogram | — | spike = algorithm issue |

**Logging**: JSON formatter — mỗi log line là 1 JSON object với `ts`, `level`,
`logger`, `msg` và các extra fields. Dễ ship vào Loki/ELK và query kiểu
`cluster_count > 5 AND confidence < 0.5`.

**Latency header**: `X-Response-Time-Ms` đính kèm mỗi response. Cho phép đo
p50/p99 bằng cách collect header từ client mà không cần Prometheus scraper.
