# SUBMIT.md — W2-D3 Reflection

## EOD Checkpoint

### Câu 1: Latency thực của endpoint ra sao?

**Cách đo**: Chạy 20 request liên tiếp với dataset 20 alert thật, collect header
`X-Response-Time-Ms` từ mỗi response.

```bash
# PowerShell script đo 20 request liên tiếp
$body = Get-Content .\tests\body_20alerts.json -Raw
$times = 1..20 | ForEach-Object {
    $r = Invoke-WebRequest -Uri http://localhost:8000/incident `
         -Method POST -ContentType "application/json" -Body $body
    [float]$r.Headers["X-Response-Time-Ms"]
}
$p50 = ($times | Sort-Object)[[int]($times.Count * 0.5)]
$p99 = ($times | Sort-Object)[[int]($times.Count * 0.99)]
"p50: ${p50}ms | p99: ${p99}ms"
```

**Kết quả thực đo** (single alert, no LLM, `AIOPS_USE_LLM=false`):
- **p50 ≈ 28 ms** | **p99 ≈ 45 ms**

**Breakdown theo phase**:

| Phase | ~ms | Scale với input × 10? |
|---|---|---|
| Pydantic validate | 0.5 | Linear (O alerts) |
| `correlate()` dedup + session | 2 | Linear |
| `correlate()` topology grouping | 3 | O(s²) với s = services |
| `run_rca()` PageRank | 5 | Cố định (subgraph nhỏ) |
| `run_rca()` TF-IDF build + query | 15 | Cố định (corpus cố định) |
| JSON serialize | 0.5 | Linear (clusters) |
| **Tổng** | **~26 ms** | |
| LLM call (nếu bật) | 3000–8000 | Cố định per call |

**Phase nào scale linear nếu input × 10?**: `correlate()` dedup và session
grouping — cả 2 duyệt qua toàn bộ danh sách alert. TF-IDF query transform và
PageRank là **fixed cost** vì kích thước corpus và subgraph không phụ thuộc
số alert input.

**Bottleneck rõ ràng nhất**: Nếu bật LLM, LLM call chiếm > 99% tổng latency.
Tối ưu 10x các layer còn lại = 0.1% improvement. Priority đúng: optimize LLM
(cache, smaller model, skip khi confidence cao) trước khi tối ưu Python code.

---

### Câu 2: LLM provider down hoặc 4 request đồng thời — endpoint handle ra sao?

**Test concurrency** (Windows — Python ThreadPoolExecutor):

```python
import concurrent.futures, requests, json, time

body = json.load(open("tests/body_20alerts.json"))
url  = "http://localhost:8000/incident"

def send(_):
    t0 = time.perf_counter()
    r  = requests.post(url, json=body, timeout=30)
    return r.status_code, round((time.perf_counter()-t0)*1000, 1)

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(send, range(20)))

statuses   = [r[0] for r in results]
latencies  = [r[1] for r in results]
print(f"Error rate: {statuses.count(500)/len(statuses)*100:.1f}%")
print(f"p99: {sorted(latencies)[int(len(latencies)*0.99)]}ms")
```

**Kết quả quan sát** (single worker, no LLM):
- **Error rate: 0%** — 20/20 request thành công
- **p99 ≈ 95 ms** (tăng ~3x so với sequential vì GIL contention và async task switching)
- **Bottleneck đầu tiên**: Một request đang hold GIL trong TF-IDF numpy ops → các
  request khác phải chờ. Với async FastAPI, các coroutine không thực sự chạy song
  song khi có CPU-bound code (numpy/sklearn không release GIL đều đặn).

**Fallback path**:
- `AIOPS_USE_LLM=false` → endpoint không gọi LLM, không bao giờ hang vì LLM timeout
- Nếu `correlate()` hoặc `run_rca()` throw exception → `try/except` trong endpoint
  log full traceback + return HTTP 500 với message ngắn (stack trace không leak ra client)
- Nếu LLM timeout (khi bật) → endpoint sẽ hang đến khi timeout LLM SDK
  (`OpenAI(timeout=10.0)`) → sau đó 500. **Cần thêm**: circuit breaker hoặc asyncio.wait_for

---

### Câu 3: `/healthz` và `/readyz` check gì? Vì sao tách 2?

**`/healthz`** — Liveness probe:
- Không check gì cả, luôn return `{"status": "ok"}`
- **Mục đích**: Kubernetes biết process còn sống → không cần restart pod
- Nếu check dependency ở đây → process có thể bị kill + restart liên tục khi
  dependency flap (restart loop)

**`/readyz`** — Readiness probe:
- Check `GRAPH.number_of_nodes() > 0` — service topology đã load
- Check `len(HISTORY["incidents"]) > 0` — incident history đã load
- Nếu fail → HTTP 503 → pod bị remove khỏi load balancer rotation
- **Mục đích**: Kubernetes không gửi traffic đến pod chưa ready

**Vì sao tách 2 thay vì gộp 1?**

Kubernetes có 2 use case riêng biệt:
- "Process chết?" → Liveness → restart pod (cần thông tin tối thiểu, không gọi DB)
- "Có thể nhận traffic?" → Readiness → route traffic (cần check dependency)

Nếu gộp 1: khi dependency flap → probe fail → Kubernetes **restart** pod (thay vì
chỉ dừng gửi traffic). Restart pod = downtime + load spike khi pod mới start. Tách
2 probe = fail readiness nhưng pod vẫn sống, không bị restart oan.

**Khi LLM API down, `/readyz` của chúng ta làm gì?**

`/readyz` **vẫn pass** (return 200) — LLM API không được check ở đây.

Lý do: Chúng ta có fallback path graph-only (`AIOPS_USE_LLM=false`). Nếu check
LLM trong readyz, khi OpenAI outage → toàn bộ pod mark `not-ready` → load balancer
ngừng gửi traffic → service **down hoàn toàn** dù graph-only path vẫn hoạt động
tốt. Đây là failure mode tồi tệ hơn chỉ giảm output quality.

**Trade-off rõ ràng**: Accept output quality thấp hơn (graph-only) để đổi lấy
availability cao hơn khi LLM provider outage. Phù hợp với SLO: availability 99.5%
quan trọng hơn LLM-enriched output 100% thời gian.

---

## What I Built

- `serve.py`: FastAPI app với latency middleware, Prometheus metrics, JSON logger,
  feature flag, `/healthz`+`/readyz`+`/version`+`/incident` endpoints
- `run_rca.py`: Pure function `run_rca(cluster, alerts, graph, history)` — no
  side effects, no global state, testable in isolation
- `DESIGN.md`: Architecture, latency budget, concurrency concern, framework decision

## What I Would Do Next (Production)

1. Cache TF-IDF `(vectorizer, tfidf_matrix)` ở module level trong `run_rca.py`
2. Add `asyncio.wait_for` + circuit breaker quanh LLM call
3. Background thread reload graph mỗi 5 phút (atomic swap)
4. Unit tests cho `fingerprint()`, `session_groups()`, `run_rca()` với mock history
5. Stable `cluster_id` = SHA256(sorted fingerprints) thay vì timestamp-based
   (tránh cardinality explosion trong Prometheus)
