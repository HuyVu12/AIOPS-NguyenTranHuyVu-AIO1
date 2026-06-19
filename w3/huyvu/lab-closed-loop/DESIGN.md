# DESIGN.md — Ronki Closed-Loop Orchestrator

## 1. Decision engine: Rule-based hay LLM-based?

**Chọn: Rule-based.**

Stack Ronki có 3 loại alert được định nghĩa rõ ràng (`HighLatency`, `HighErrorRate`, `InstanceDown`) và mỗi loại map 1-1 với một runbook đã được ops team kiểm chứng trước. Trong môi trường này, rule-based cho **latency quyết định < 1ms** và hoàn toàn **deterministic** — cùng một alert luôn trigger cùng một runbook, không phụ thuộc vào trạng thái ngoài.

### Trade-off

| Tiêu chí | Rule-based | LLM-based |
|---|---|---|
| Latency quyết định | < 1 ms (dict lookup) | 200–800 ms (API round-trip) |
| Determinism | 100% — cùng input → cùng output | Phụ thuộc vào temperature, prompt version |
| Mở rộng alert mới | Phải thêm dòng vào `config.yaml` thủ công | Có thể tự suy luận nếu prompt đủ tốt |
| Chi phí vận hành | Không | ~$0.002–0.01 / quyết định (Anthropic API) |
| Khả năng sẵn sàng | Không phụ thuộc network ngoài | Cần fallback khi API unreachable |
| Nguy cơ hallucination | Không (enum hữu hạn) | Có (cần validation layer — xem mục 5) |

**Kết luận:** với 3 alert type cố định và yêu cầu reliability cao trong production, rule-based là lựa chọn đúng đắn. Nếu mở rộng lên 20+ alert type với mô tả tự nhiên phức tạp (log anomaly, tracing span, business metric), sẽ xem xét LLM-based với `confidence >= 0.6` và fallback về rule-based khi API không khả dụng.

---

## 2. Blast-radius config

```yaml
blast_radius:
  max_actions_per_minute: 3
  max_restarts_per_service_per_hour: 5
```

### Lý do chọn giá trị cụ thể

**`max_actions_per_minute: 3`**

Stack có 5 service. Nếu cascade failure xảy ra đồng loạt, giới hạn 3 action/phút đảm bảo orchestrator không restart tất cả 5 service trong cùng một phút — tránh gây **thundering herd** lên database và load balancer. 3 action/phút = đủ phản ứng nhanh cho 3 service ưu tiên cao, trong khi các service còn lại đợi sang chu kỳ tiếp theo.

Ví dụ tính toán: payment-svc (critical) + checkout-svc (critical) + inventory-svc (medium) = 3. frontend và api-gateway thường self-recover nhanh hơn.

**`max_restarts_per_service_per_hour: 5`**

Nếu một service bị restart > 5 lần trong 1 giờ mà vẫn fail, đây là dấu hiệu lỗi không tự phục hồi được: OOM liên tục, config sai, dependency downstream down, disk đầy. Tiếp tục restart là vô nghĩa và tốn tài nguyên — cần human escalation. 5 lần đủ để xử lý các transient failure (network glitch, pod eviction) mà không mở cửa cho restart storm.

**Hành vi khi vượt ngưỡng:** log `BLAST_RADIUS_EXCEEDED`, không thực thi action, alert tiếp tục firing cho đến khi human can thiệp.

---

## 3. Verify step

### Metric kiểm tra

Verify step kiểm tra **đồng thời 2 metric**:
1. **`latency_p99`** (ms) — từ `histogram_quantile(0.99, rate(...))`
2. **`up`** (1/0) — service phải reachable trước khi latency có ý nghĩa

### Threshold

| Metric | Threshold | Nguồn |
|---|---|---|
| `latency_p99_max_ms` | **500 ms** | ~2x baseline p99 của service chậm nhất (checkout-svc: 230 ms). Đủ rộng để tránh false negative nhưng vẫn phát hiện nếu action không có hiệu quả. |
| `up_required` | **1** | Service phải reachable; `up=0` là fail tuyệt đối. |

### Timeout và polling

| Tham số | Giá trị | Lý do |
|---|---|---|
| `verify_timeout_seconds` | **60 s** | Restart container mất 5–10 s; sau đó cần 15–20 s để metric ổn định qua ít nhất 2 Prometheus scrape (interval 10 s). 60 s = đủ cho 6 poll cycle sau khi container up. |
| `verify_poll_interval_seconds` | **10 s** | Match với scrape interval của Prometheus — poll nhanh hơn không có ý nghĩa vì metric chưa được cập nhật. |
| `verify_min_samples` | **3** | Yêu cầu 3 sample **liên tiếp** đều pass trước khi kết luận thành công. Một sample may mắn tốt ngay sau khi inject không đủ bằng chứng recovery. |

---

## 4. Circuit breaker reset

**Reset mode: Manual.**

### Lý do chọn manual reset

Circuit breaker mở khi 3 consecutive failure xảy ra. Đây là trạng thái bất thường nghiêm trọng — orchestrator đã thử và thất bại 3 lần liên tiếp. Nếu tự động reset sau N phút, rủi ro là:

- Orchestrator tiếp tục loop vô hạn gây thêm disruption (thundering herd, connection pool exhaustion)
- Không có kỹ sư nào xem xét nguyên nhân gốc rễ trước khi automation tiếp tục
- Các lần restart không có mục đích sẽ gây noise cho monitoring

Manual reset đảm bảo: một kỹ sư **xem log → xác định root cause → xác nhận fix xong** trước khi tiếp tục automation.

**Chi phí:** vài phút delay để kỹ sư can thiệp, chấp nhận được so với rủi ro automation sai.

**Cách reset:** `Ctrl+C` dừng orchestrator → sửa sự cố → khởi động lại:
```bash
uv run python closed_loop.py --config config.yaml
```

**Nếu muốn automatic reset trong tương lai:** thêm `cool_down_seconds: 1800` (30 phút) vào config và implement time-based reset, kết hợp với PagerDuty alert khi circuit mở.

---

## 5. Per-service mutex (Stress S2 — Concurrent alert race)

**Thiết kế:** Một `threading.Lock` riêng biệt cho mỗi service name, lưu trong dict `_service_locks` được bảo vệ bởi một meta-lock. Khi alert đến, orchestrator gọi `acquire(blocking=False)`:
- Nếu lock free → acquire, thực thi runbook, release khi xong
- Nếu lock busy → log `SERVICE_LOCK_BUSY` và return ngay (không xếp hàng chờ)

**Tại sao `blocking=False` thay vì queue?**

Trong closed-loop production, một runbook đang chạy trên service A là sự kiện đang tiến hành. Alert mới trên cùng service A trong vòng 30 s là **duplicate của cùng sự cố**, không phải sự cố mới. Xếp hàng chờ sẽ khiến orchestrator re-execute runbook ngay sau khi lock release — tức là restart 2 lần liên tiếp mà không kiểm tra xem lần đầu có resolve chưa. Nguy hiểm hơn là skip.

**Hai service khác nhau:** có lock khác nhau → luôn chạy song song, không block nhau. `DRY_RUN_PASS` timestamps của payment-svc và inventory-svc sẽ cách nhau < 1 s trong cùng poll cycle.

---

## 6. Transactional multi-step rollback (Stress S1)

**Thiết kế:** `run_transactional_steps()` thực thi steps A → B → C, tích lũy danh sách `completed` theo thứ tự thực hiện. Khi bất kỳ step nào fail:

1. Log `TRANSACTIONAL_STEP_FAIL` với `completed_before_failure`
2. Lấy `rollback_steps[:len(completed)]` — chỉ rollback những step đã thực thi thành công
3. Duyệt `reversed()` → rollback-B trước, rollback-A sau (LIFO)
4. Log `TRANSACTIONAL_ROLLBACK_COMPLETE` với danh sách đã rollback

**Tại sao LIFO là đúng về kỹ thuật:**

Step A (drain traffic) tạo state mà step B (apply config) phụ thuộc vào. Nếu rollback A trước B, service nhận traffic trong khi config đang ở trạng thái không nhất quán. LIFO đảm bảo teardown đi ngược với setup — cùng nguyên lý với transaction rollback trong database (undo log) và dependency injection teardown.

**Quan trọng:** Không log `ACTION_SUCCESS` khi deploy fail — đây là lỗi thiết kế phổ biến làm mislead monitoring.

---

## 7. Decision validation (Stress S3 — LLM hallucination defense)

**Thiết kế:** Trước khi gọi dry-run, `validate_runbook()` kiểm tra tên runbook có nằm trong `runbook_registry` (whitelist tường minh trong `config.yaml`) hay không. Nếu không:
- Log `DECISION_VALIDATION_FAILED` với đầy đủ 4 field: `bad_runbook`, `alertname`, `raw_decision`, `action=escalate_no_auto_action`
- Return ngay — **không spawn subprocess, không thực thi gì**
- Circuit breaker counter **không tăng** (validation failure ≠ action failure)

**Tại sao cần whitelist tường minh:**

LLM có thể trả về tên runbook hợp lý về mặt ngôn ngữ nhưng không tồn tại trong hệ thống (`scale_down_database.sh`, `reboot_kernel.sh`). Nếu orchestrator tin tưởng và chạy `subprocess` với path không tồn tại, bash exit non-zero và fail sẽ increment circuit breaker. Sau 3 hallucination liên tiếp, circuit sẽ mở — **automation bị halt không phải vì service gặp sự cố, mà vì LLM hallucinate**. Validation trước dry-run ngắt vòng lặp đó hoàn toàn.

---

## 8. Metrics cho observability

5 metric được chọn theo nguyên tắc **debug-driven** — mỗi metric trả lời một câu hỏi cụ thể khi incident xảy ra:

| Metric | Câu hỏi trả lời |
|---|---|
| `closed_loop_actions_total{outcome}` | Orchestrator đang act, rollback hay thất bại? Không cần đọc log. |
| `closed_loop_circuit_breaker_state` | Tại sao không có action nào được thực thi? Gauge=1 → cần restart orchestrator thủ công. |
| `closed_loop_blast_radius_remaining` | Orchestrator im lặng vì không có alert hay vì đã dùng hết quota? |
| `closed_loop_mutex_locked` | Service X bị LOCKED > 5 phút → runbook đang bị treo, cần kill process. |
| `closed_loop_verify_status` | Verify đang chờ Prometheus confirm (=2) hay đã pass/fail? |
