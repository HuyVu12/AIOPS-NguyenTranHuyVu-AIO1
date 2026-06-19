# SUBMIT.md — Kết quả chạy 6 chaos scenarios (3 bắt buộc + 3 stress)

## Thông tin

- Họ tên: Huy Vũ
- Decision engine: Rule-based (`runbook_map` trong `config.yaml`)
- Python: 3.11/3.12, uv 0.4.x
- Docker Compose: v2
- Tất cả log dưới đây là log thật, lấy trực tiếp từ `audit_log_scenario{1..6}.jsonl` sau khi chạy lại toàn bộ stack — không có log nào được dựng tay hoặc giả lập.

---

## Scenario 1 — Action thành công (latency inject trên payment-svc)

**Điều kiện:** `baseline.json` giữ nguyên `latency_p99_max_ms = 500`.

**Lệnh inject (giả lập qua Alertmanager API):**
```bash
python -c "import requests; requests.post('http://localhost:9093/api/v2/alerts', json=[{'labels': {'alertname': 'HighLatency', 'service': 'payment-svc', 'severity': 'warning'}, 'annotations': {'summary': 'Test High Latency'}, 'generatorURL': 'http://prometheus:9090'}])"
```

**Log orchestrator:**
```json
{"ts": "2026-06-19T03:29:09.198951+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "HighLatency", "service": "payment-svc", "severity": "warning", "fingerprint": "162554e46348b70f"}
{"ts": "2026-06-19T03:29:09.200846+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "DECIDE_RUNBOOK", "alertname": "HighLatency", "service": "payment-svc", "runbook": "runbooks/restart_service.sh"}
{"ts": "2026-06-19T03:29:09.218397+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "BLAST_RADIUS_OK", "service": "payment-svc", "remaining_global": 3}
{"ts": "2026-06-19T03:29:09.219757+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_EXEC", "script": "runbooks/restart_service.sh", "service": "payment-svc", "dry_run": true, "extra_args": []}
{"ts": "2026-06-19T03:29:09.423559+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_RESULT", "script": "runbooks/restart_service.sh", "service": "payment-svc", "dry_run": true, "returncode": 0, "stdout": "[DRY-RUN] would execute: docker restart ronki-payment-svc", "stderr": ""}
{"ts": "2026-06-19T03:29:09.425573+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "DRY_RUN_PASS", "runbook": "runbooks/restart_service.sh", "service": "payment-svc"}
{"ts": "2026-06-19T03:29:09.428130+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_EXEC", "script": "runbooks/restart_service.sh", "service": "payment-svc", "dry_run": false, "extra_args": []}
{"ts": "2026-06-19T03:29:26.674437+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_RESULT", "script": "runbooks/restart_service.sh", "service": "payment-svc", "dry_run": false, "returncode": 0, "stdout": "[restart_service] Restarting container: ronki-payment-svc ...\nronki-payment-svc\n[restart_service] Waiting 5 s for ronki-payment-svc to stabilise ...\n[restart_service] ronki-payment-svc is running. SUCCESS.", "stderr": ""}
{"ts": "2026-06-19T03:29:26.675938+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ACTION_EXECUTED", "runbook": "runbooks/restart_service.sh", "service": "payment-svc"}
{"ts": "2026-06-19T03:29:26.677214+00:00", "level": "INFO", "logger": "verify", "event_type": "VERIFY_START", "service": "payment-svc", "timeout_s": 60, "latency_threshold_ms": 500, "min_samples": 3}
{"ts": "2026-06-19T03:29:26.723050+00:00", "level": "INFO", "logger": "verify", "event_type": "VERIFY_SAMPLE", "service": "payment-svc", "sample": 1, "latency_p99_ms": 248.07, "up": 1.0, "latency_ok": true, "up_ok": true}
{"ts": "2026-06-19T03:29:36.777665+00:00", "level": "INFO", "logger": "verify", "event_type": "VERIFY_SAMPLE", "service": "payment-svc", "sample": 2, "latency_p99_ms": 247.96, "up": 1.0, "latency_ok": true, "up_ok": true}
{"ts": "2026-06-19T03:29:46.820875+00:00", "level": "INFO", "logger": "verify", "event_type": "VERIFY_SAMPLE", "service": "payment-svc", "sample": 3, "latency_p99_ms": 248.11, "up": 1.0, "latency_ok": true, "up_ok": true}
{"ts": "2026-06-19T03:29:46.824017+00:00", "level": "INFO", "logger": "verify", "event_type": "VERIFY_PASS", "service": "payment-svc", "samples": 3, "consecutive_passes": 3}
{"ts": "2026-06-19T03:29:46.825679+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ACTION_SUCCESS", "alertname": "HighLatency", "service": "payment-svc", "runbook": "runbooks/restart_service.sh"}
```

**Kết quả:** PASS. p99 latency dưới ngưỡng 500ms (~248ms) được xác minh qua 3 mẫu liên tiếp từ Prometheus. Dịch vụ hồi phục thành công và log `ACTION_SUCCESS`. Sau scenario này, orchestrator được dừng (Ctrl+C) trước khi chuyển sang scenario 2.

---

## Scenario 2 — Action fail → rollback (payment-svc latency, checkout-svc InstanceDown)

**Điều kiện:** Backup `baseline.json` rồi hạ `latency_p99_max_ms` xuống `1` để verify luôn fail (mục đích kiểm thử logic rollback, không phản ánh ngưỡng production thật).

```bash
cp data-pack/data/baseline.json data-pack/data/baseline.json.bak
# sửa latency_p99_max_ms: 500 → 1
```

**Phần 1 — HighLatency trên payment-svc:**
```json
{"ts": "2026-06-19T03:30:47.705129+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "HighLatency", "service": "payment-svc", "severity": "warning", "fingerprint": "162554e46348b70f"}
{"ts": "2026-06-19T03:30:47.707958+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "DECIDE_RUNBOOK", "alertname": "HighLatency", "service": "payment-svc", "runbook": "runbooks/restart_service.sh"}
{"ts": "2026-06-19T03:30:48.181362+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "BLAST_RADIUS_OK", "service": "payment-svc", "remaining_global": 3}
{"ts": "2026-06-19T03:30:48.417342+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "DRY_RUN_PASS", "runbook": "runbooks/restart_service.sh", "service": "payment-svc"}
{"ts": "2026-06-19T03:31:06.887503+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ACTION_EXECUTED", "runbook": "runbooks/restart_service.sh", "service": "payment-svc"}
{"ts": "2026-06-19T03:31:06.888977+00:00", "level": "INFO", "logger": "verify", "event_type": "VERIFY_START", "service": "payment-svc", "timeout_s": 60, "latency_threshold_ms": 1, "min_samples": 3}
{"ts": "2026-06-19T03:31:06.917617+00:00", "level": "INFO", "logger": "verify", "event_type": "VERIFY_SAMPLE", "service": "payment-svc", "sample": 1, "latency_p99_ms": 248.21, "up": 1.0, "latency_ok": false, "up_ok": true}
{"ts": "2026-06-19T03:31:57.635034+00:00", "level": "INFO", "logger": "verify", "event_type": "VERIFY_SAMPLE", "service": "payment-svc", "sample": 6, "latency_p99_ms": 248.12, "up": 1.0, "latency_ok": false, "up_ok": true}
{"ts": "2026-06-19T03:32:07.639122+00:00", "level": "WARNING", "logger": "verify", "event_type": "VERIFY_FAIL", "service": "payment-svc", "samples": 6, "timeout_s": 60}
{"ts": "2026-06-19T03:32:07.639652+00:00", "level": "WARNING", "logger": "orchestrator", "event_type": "ROLLBACK_TRIGGERED", "service": "payment-svc", "rollback_runbook": "runbooks/restart_service.sh", "alertname": "HighLatency"}
{"ts": "2026-06-19T03:32:24.615799+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ROLLBACK_EXECUTED", "service": "payment-svc", "rollback_runbook": "runbooks/restart_service.sh", "rollback_success": true}
{"ts": "2026-06-19T03:32:24.616799+00:00", "level": "WARNING", "logger": "safety", "event_type": "CIRCUIT_BREAKER_FAILURE", "consecutive_failures": 1, "threshold": 3}
```

**Phần 2 — InstanceDown trên checkout-svc (kill container thật):**
```json
{"ts": "2026-06-19T03:32:39.650241+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "InstanceDown", "service": "checkout-svc", "severity": "critical", "fingerprint": "49e60b200c4d780d"}
{"ts": "2026-06-19T03:32:39.651605+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "DECIDE_RUNBOOK", "alertname": "InstanceDown", "service": "checkout-svc", "runbook": "runbooks/restart_service.sh"}
{"ts": "2026-06-19T03:32:46.112441+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ACTION_EXECUTED", "runbook": "runbooks/restart_service.sh", "service": "checkout-svc"}
{"ts": "2026-06-19T03:32:46.114062+00:00", "level": "INFO", "logger": "verify", "event_type": "VERIFY_START", "service": "checkout-svc", "timeout_s": 60, "latency_threshold_ms": 1, "min_samples": 3}
{"ts": "2026-06-19T03:32:46.152098+00:00", "level": "INFO", "logger": "verify", "event_type": "VERIFY_SAMPLE", "service": "checkout-svc", "sample": 1, "latency_p99_ms": null, "up": 0.0, "latency_ok": false, "up_ok": false}
{"ts": "2026-06-19T03:33:46.389529+00:00", "level": "WARNING", "logger": "verify", "event_type": "VERIFY_FAIL", "service": "checkout-svc", "samples": 6, "timeout_s": 60}
{"ts": "2026-06-19T03:33:46.390278+00:00", "level": "WARNING", "logger": "orchestrator", "event_type": "ROLLBACK_TRIGGERED", "service": "checkout-svc", "rollback_runbook": "runbooks/restart_service.sh", "alertname": "InstanceDown"}
{"ts": "2026-06-19T03:34:03.206726+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ROLLBACK_EXECUTED", "service": "checkout-svc", "rollback_runbook": "runbooks/restart_service.sh", "rollback_success": true}
{"ts": "2026-06-19T03:34:03.207912+00:00", "level": "WARNING", "logger": "safety", "event_type": "CIRCUIT_BREAKER_FAILURE", "consecutive_failures": 2, "threshold": 3}
```

**Kết quả:** PASS. Cả hai lần verify đều fail đúng như thiết kế (threshold ép xuống 1ms), trigger rollback tự động, và `consecutive_failures` tăng dần 1 → 2. `sample 1` của checkout-svc cho thấy `up=0.0` ngay sau khi container restart — đúng chu kỳ container chưa kịp re-register với Prometheus, sample sau đó mới lên `up=1.0`.

---

## Scenario 3 — Circuit breaker (3 consecutive failures, 3 service khác nhau)

**Điều kiện:** Giữ nguyên threshold `1ms` từ scenario 2. Restart orchestrator (circuit breaker reset thủ công sau scenario 2 vì failures đã ở mức 2/3 — cần phiên mới sạch để demo đúng kịch bản "3 alert độc lập gây HALT").

Gửi tuần tự, đợi rollback của từng alert hoàn tất trước khi gửi alert kế tiếp:
1. `HighLatency` → `payment-svc`
2. `HighLatency` → `inventory-svc`
3. `HighLatency` → `checkout-svc`

```json
{"ts": "2026-06-19T03:35:06.605518+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "HighLatency", "service": "payment-svc", "severity": "warning", "fingerprint": "162554e46348b70f"}
{"ts": "2026-06-19T03:36:23.738366+00:00", "level": "WARNING", "logger": "orchestrator", "event_type": "ROLLBACK_TRIGGERED", "service": "payment-svc", "rollback_runbook": "runbooks/restart_service.sh", "alertname": "HighLatency"}
{"ts": "2026-06-19T03:36:40.658965+00:00", "level": "WARNING", "logger": "safety", "event_type": "CIRCUIT_BREAKER_FAILURE", "consecutive_failures": 1, "threshold": 3}

{"ts": "2026-06-19T03:37:10.723492+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "HighLatency", "service": "inventory-svc", "severity": "warning", "fingerprint": "24c917f8fe0866ad"}
{"ts": "2026-06-19T03:38:34.739594+00:00", "level": "WARNING", "logger": "orchestrator", "event_type": "ROLLBACK_TRIGGERED", "service": "inventory-svc", "rollback_runbook": "runbooks/restart_service.sh", "alertname": "HighLatency"}
{"ts": "2026-06-19T03:38:52.869890+00:00", "level": "WARNING", "logger": "safety", "event_type": "CIRCUIT_BREAKER_FAILURE", "consecutive_failures": 2, "threshold": 3}

{"ts": "2026-06-19T03:39:22.908420+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "HighLatency", "service": "checkout-svc", "severity": "warning", "fingerprint": "9ebd09cb247251a5"}
{"ts": "2026-06-19T03:40:41.342617+00:00", "level": "WARNING", "logger": "orchestrator", "event_type": "ROLLBACK_TRIGGERED", "service": "checkout-svc", "rollback_runbook": "runbooks/restart_service.sh", "alertname": "HighLatency"}
{"ts": "2026-06-19T03:40:58.418086+00:00", "level": "WARNING", "logger": "safety", "event_type": "CIRCUIT_BREAKER_FAILURE", "consecutive_failures": 3, "threshold": 3}
{"ts": "2026-06-19T03:40:58.419601+00:00", "level": "ERROR", "logger": "safety", "event_type": "CIRCUIT_BREAKER_HALT", "consecutive_failures": 3, "threshold": 3, "message": "Automation halted. Manual intervention required."}
{"ts": "2026-06-19T03:41:13.432602+00:00", "level": "ERROR", "logger": "orchestrator", "event_type": "CIRCUIT_BREAKER_HALT", "message": "Circuit OPEN — polling suspended. Manual reset required."}
```

**Kết quả:** PASS. Sau lỗi thứ 3 liên tiếp (`checkout-svc`), circuit chuyển `OPEN` ngay lập tức (`CIRCUIT_BREAKER_HALT` từ module `safety`). Vòng poll kế tiếp của orchestrator bị đình chỉ và log lại `CIRCUIT_BREAKER_HALT` từ logger `orchestrator`, xác nhận polling đã suspend đúng thiết kế.

**Sau scenario:** Restore `baseline.json` từ backup, đưa `latency_p99_max_ms` về `500`. Restart orchestrator để reset circuit breaker (manual reset mode) trước khi chạy scenario 4.

```bash
cp data-pack/data/baseline.json.bak data-pack/data/baseline.json
```

---

## Scenario 4 — Transactional rollback (Stress S1) — MultiStepDeploy trên api-gateway

**Setup:**
1. Uncomment `multi_step_map` / `multi_step_rollback_map` trong `config.yaml`, thêm `MultiStepDeploy: "runbooks/multi_step_deploy.sh"` vào `runbook_map`.
2. Tạm sửa `multi_step_deploy.sh`, case `C` (nhánh **real execution**, không phải `--dry-run`) để force-fail:
   ```bash
   C)
       echo "[multi_step_deploy] step-C: re-enabling traffic for $CONTAINER ..."
       echo "[multi_step_deploy] step-C: FORCED FAILURE for testing rollback"
       exit 1
       ;;
   ```
3. Gửi alert `MultiStepDeploy` trên `api-gateway` qua Alertmanager API (đồng thời với một `HighLatency` trên `checkout-svc` để kiểm tra 2 luồng song song không giao thoa).

**Log (rút gọn, giữ đúng trình tự các event quan trọng):**
```json
{"ts": "2026-06-19T03:43:27.944197+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "MultiStepDeploy", "service": "api-gateway", "severity": "critical", "fingerprint": "cc33ddd14519f0a0"}
{"ts": "2026-06-19T03:43:27.952358+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "DECIDE_RUNBOOK", "alertname": "MultiStepDeploy", "service": "api-gateway", "runbook": "runbooks/multi_step_deploy.sh"}
{"ts": "2026-06-19T03:43:28.196833+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "DRY_RUN_PASS", "runbook": "runbooks/multi_step_deploy.sh", "service": "api-gateway"}

{"ts": "2026-06-19T03:43:28.197955+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_EXEC", "script": "runbooks/multi_step_deploy.sh", "service": "api-gateway", "dry_run": false, "extra_args": ["--step-a"]}
{"ts": "2026-06-19T03:43:39.909574+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "TRANSACTIONAL_STEP_COMPLETE", "step": "runbooks/multi_step_deploy.sh", "service": "api-gateway"}

{"ts": "2026-06-19T03:43:39.911051+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_EXEC", "script": "runbooks/multi_step_deploy.sh", "service": "api-gateway", "dry_run": false, "extra_args": ["--step-b"]}
{"ts": "2026-06-19T03:43:45.086167+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "TRANSACTIONAL_STEP_COMPLETE", "step": "runbooks/multi_step_deploy.sh", "service": "api-gateway"}

{"ts": "2026-06-19T03:43:45.088081+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_EXEC", "script": "runbooks/multi_step_deploy.sh", "service": "api-gateway", "dry_run": false, "extra_args": ["--step-c"]}
{"ts": "2026-06-19T03:43:45.494592+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_RESULT", "script": "runbooks/multi_step_deploy.sh", "service": "api-gateway", "dry_run": false, "returncode": 1, "stdout": "[multi_step_deploy] step-C: re-enabling traffic for ronki-api-gateway ...\n[multi_step_deploy] step-C: FORCED FAILURE for testing rollback", "stderr": ""}
{"ts": "2026-06-19T03:43:45.496200+00:00", "level": "ERROR", "logger": "orchestrator", "event_type": "TRANSACTIONAL_STEP_FAIL", "step": "runbooks/multi_step_deploy.sh", "service": "api-gateway", "completed_before_failure": ["runbooks/multi_step_deploy.sh", "runbooks/multi_step_deploy.sh"]}

{"ts": "2026-06-19T03:43:45.497188+00:00", "level": "WARNING", "logger": "orchestrator", "event_type": "TRANSACTIONAL_ROLLBACK_STEP", "step": "runbooks/multi_step_deploy.sh", "service": "api-gateway"}
{"ts": "2026-06-19T03:43:45.502773+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_EXEC", "script": "runbooks/multi_step_deploy.sh", "service": "api-gateway", "dry_run": false, "extra_args": ["--rollback-b"]}
{"ts": "2026-06-19T03:44:02.376935+00:00", "level": "WARNING", "logger": "orchestrator", "event_type": "TRANSACTIONAL_ROLLBACK_STEP", "step": "runbooks/multi_step_deploy.sh", "service": "api-gateway"}
{"ts": "2026-06-19T03:44:02.377937+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_EXEC", "script": "runbooks/multi_step_deploy.sh", "service": "api-gateway", "dry_run": false, "extra_args": ["--rollback-a"]}
{"ts": "2026-06-19T03:44:05.014614+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "TRANSACTIONAL_ROLLBACK_COMPLETE", "service": "api-gateway", "rolled_back": ["runbooks/multi_step_deploy.sh", "runbooks/multi_step_deploy.sh"]}
{"ts": "2026-06-19T03:44:05.016156+00:00", "level": "WARNING", "logger": "safety", "event_type": "CIRCUIT_BREAKER_FAILURE", "consecutive_failures": 1, "threshold": 3}
```

**Kết quả:** PASS. Trình tự `extra_args` thực tế của từng `RUNBOOK_EXEC` xác nhận đúng thứ tự LIFO:

```
--step-a → --step-b → --step-c (returncode=1) → --rollback-b → --rollback-a
```

`TRANSACTIONAL_STEP_FAIL` ghi rõ `completed_before_failure` gồm 2 step (A, B) — đúng số step đã hoàn thành trước khi C fail. `TRANSACTIONAL_ROLLBACK_COMPLETE` log lại `rolled_back` với 2 phần tử theo đúng thứ tự rollback-B trước, rollback-A sau. Không có `ACTION_SUCCESS` nào bị log nhầm cho luồng api-gateway. Đồng thời, `checkout-svc` (alert song song khác) vẫn verify và rollback độc lập, không bị block bởi luồng api-gateway — chứng minh per-service mutex hoạt động đúng giữa hai service khác nhau.

**Revert sau scenario (checklist 3 bước, không bỏ sót):**
1. Sửa lại case `C` trong `multi_step_deploy.sh` về bản gốc (`exit 0`, không in dòng FORCED FAILURE).
2. Re-comment `multi_step_map` / `multi_step_rollback_map` trong `config.yaml`.
3. Gỡ dòng `MultiStepDeploy: "runbooks/multi_step_deploy.sh"` khỏi `runbook_map`.

---

## Scenario 5 — Concurrent alert race + SERVICE_LOCK_BUSY (Stress S2)

**Setup:** Restart orchestrator (circuit breaker reset). Gửi đồng thời 2 alert trên 2 service khác nhau (`payment-svc`, `inventory-svc`), và 1 alert thứ hai (`HighErrorRate`) trên **cùng** `payment-svc` ngay khi runbook đầu tiên còn đang chạy.

```json
{"ts": "2026-06-19T03:45:10.678498+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "HighLatency", "service": "payment-svc", "severity": "warning", "fingerprint": "162554e46348b70f"}
{"ts": "2026-06-19T03:45:10.681050+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "HighLatency", "service": "inventory-svc", "severity": "warning", "fingerprint": "24c917f8fe0866ad"}
{"ts": "2026-06-19T03:45:10.681709+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "HighErrorRate", "service": "payment-svc", "severity": "critical", "fingerprint": "bbda17ad428351c2"}

{"ts": "2026-06-19T03:45:10.687013+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_EXEC", "script": "runbooks/restart_service.sh", "service": "payment-svc", "dry_run": true, "extra_args": []}
{"ts": "2026-06-19T03:45:10.689013+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "RUNBOOK_EXEC", "script": "runbooks/restart_service.sh", "service": "inventory-svc", "dry_run": true, "extra_args": []}
{"ts": "2026-06-19T03:45:10.691512+00:00", "level": "WARNING", "logger": "orchestrator", "event_type": "SERVICE_LOCK_BUSY", "service": "payment-svc", "message": "Another runbook is already executing for this service; skipping duplicate alert"}

{"ts": "2026-06-19T03:45:28.443988+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ACTION_SUCCESS", "alertname": "HighLatency", "service": "payment-svc", "runbook": "runbooks/restart_service.sh"}
{"ts": "2026-06-19T03:45:58.550233+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ACTION_SUCCESS", "alertname": "HighLatency", "service": "inventory-svc", "runbook": "runbooks/restart_service.sh"}
```

**Kết quả:** PASS, cả 2 nhánh xác nhận:
- **Concurrent khác service:** `RUNBOOK_EXEC` của `payment-svc` (03:45:10.687013) và `inventory-svc` (03:45:10.689013) cách nhau 2ms — cùng poll cycle, chạy song song không block nhau. Cả hai `ACTION_SUCCESS` đều log thành công độc lập.
- **SERVICE_LOCK_BUSY cùng service:** alert `HighErrorRate` đến trên `payment-svc` ngay khi `HighLatency` đang dry-run cho cùng service → `SERVICE_LOCK_BUSY` log đúng 3ms sau, và **không** có `RUNBOOK_EXEC` nào tiếp theo cho `HighErrorRate` trong toàn bộ phiên — xác nhận alert bị skip đúng thiết kế (không xếp hàng, không re-execute).

**Ghi chú:** Log còn xuất hiện `NO_RUNBOOK_MAPPING` cho alert `MultiStepDeploy` trên `api-gateway` — đây là alert tồn đọng từ Scenario 4 (chưa resolve/expire trong Alertmanager), và vì `runbook_map` đã được revert đúng quy trình (gỡ `MultiStepDeploy` sau scenario 4) nên orchestrator không tìm thấy mapping — đúng hành vi mong đợi, không phải lỗi.

---

## Scenario 6 — LLM Hallucination Defense (Stress S3)

**Setup:** Uncomment `TestHallucination: "runbooks/nonexistent_runbook.sh"` trong `runbook_map`, **không** thêm vào `runbook_registry`. Restart orchestrator để đảm bảo circuit breaker ở trạng thái CLOSED sạch trước khi test (tránh nhiễu kết quả do circuit cũ còn OPEN). Gửi alert `TestHallucination` trên `payment-svc` sau khi 2 alert `HighLatency` khác (payment-svc, inventory-svc) đã verify PASS thành công, để chứng minh hệ thống vẫn hoạt động bình thường trước khi hit hallucination case.

```json
{"ts": "2026-06-19T03:47:14.688487+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ACTION_SUCCESS", "alertname": "HighLatency", "service": "inventory-svc", "runbook": "runbooks/restart_service.sh"}
{"ts": "2026-06-19T03:47:24.727384+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ACTION_SUCCESS", "alertname": "HighLatency", "service": "payment-svc", "runbook": "runbooks/restart_service.sh"}

{"ts": "2026-06-19T03:47:39.754338+00:00", "level": "INFO", "logger": "orchestrator", "event_type": "ALERT_DETECTED", "alertname": "TestHallucination", "service": "payment-svc", "severity": "warning", "fingerprint": "662dc5592370a610"}
{"ts": "2026-06-19T03:47:39.756172+00:00", "level": "ERROR", "logger": "orchestrator", "event_type": "DECISION_VALIDATION_FAILED", "bad_runbook": "runbooks/nonexistent_runbook.sh", "alertname": "TestHallucination", "raw_decision": "runbooks/nonexistent_runbook.sh", "action": "escalate_no_auto_action"}
```

**Kết quả:** PASS. `DECISION_VALIDATION_FAILED` log đầy đủ 4 field bắt buộc (`bad_runbook`, `alertname`, `raw_decision`, `action=escalate_no_auto_action`). Sau event này, **không** có `RUNBOOK_EXEC`, `DRY_RUN_PASS`, hay `RUNBOOK_RESULT` nào được ghi cho `TestHallucination` trong toàn bộ log — xác nhận không subprocess nào được spawn. `CIRCUIT_BREAKER_FAILURE` cũng không tăng sau event này, đúng thiết kế "validation failure ≠ action failure".

**Revert sau scenario:** Re-comment `TestHallucination` khỏi `runbook_map`.

---

## Issues Encountered & Fixes

### 1. `RUNBOOK_ERROR: [WinError 2] The system cannot find the file specified` (lần chạy đầu, trên Windows)

Lần chạy thử đầu tiên (trước khi thu thập log chính thức) gặp lỗi `subprocess.run(["bash", script, ...])` không tìm thấy `bash` trên PATH — vì máy chạy native Windows, không có `bash` trong `PATH` hệ thống mặc định. Lỗi này khiến `DRY_RUN_FAIL` xảy ra ngay từ bước dry-run đầu tiên, chặn toàn bộ pipeline.

**Cách fix:** Chạy orchestrator từ Git Bash (đi kèm Git for Windows, có `bash.exe` sẵn trong PATH của shell đó), đảm bảo current working directory là `sample-solution/` khi gọi `uv run python closed_loop.py` để các đường dẫn tương đối (`runbooks/...`, `--config config.yaml`) resolve đúng. Sau khi đổi sang Git Bash, toàn bộ 6 scenario chạy không còn gặp lỗi này.

### 2. Forced failure cho Scenario 4 (transactional rollback)

Vì `multi_step_deploy.sh` mặc định không có cách nào tự fail ở step-C trong môi trường lab thật (container vẫn `docker start` được bình thường), nhóm chọn cách tạm sửa trực tiếp case `C` (nhánh real execution) để in thông báo và `exit 1`, giả lập một lỗi deploy thật (ví dụ: health-check fail sau khi traffic được bật lại). Đây cùng tinh thần với cách `expected.json` đề xuất cho scenario 2 (hạ threshold để ép verify fail) — sửa tạm một điểm rõ ràng để kiểm thử logic rollback, sau đó revert hoàn toàn theo checklist 3 bước ở Scenario 4.

### 3. Backup & restore `baseline.json`

Để tránh việc sửa tay rồi quên revert (rủi ro để threshold `1ms` lẫn vào baseline thật), nhóm luôn `cp` file gốc thành `.bak` trước khi sửa threshold cho Scenario 2/3, và restore lại ngay sau khi Scenario 3 hoàn tất, trước khi chạy Scenario 4:

```bash
cp data-pack/data/baseline.json data-pack/data/baseline.json.bak
# ... sửa threshold, test scenario 2 + 3 ...
cp data-pack/data/baseline.json.bak data-pack/data/baseline.json
```

Đã xác nhận lại `latency_p99_max_ms` trong `baseline.json` = `500` trước khi chạy Scenario 4, 5, 6.

### 4. `seen` fingerprint set — fix cho phép alert tái phát được xử lý lại

Phiên bản đầu của `closed_loop.py` dùng `seen: set[str]` chỉ-thêm-không-xóa (clear toàn bộ mỗi 500 phần tử) — nghĩa là một alert đã fire một lần thì fingerprint của nó vĩnh viễn bị bỏ qua, kể cả khi alert tự resolve rồi fire lại sau đó. Đã sửa bằng cách intersect `seen` với tập fingerprint đang active mỗi vòng poll, **trước** khi lọc alert mới:

```python
alerts = fetch_active_alerts(cfg["alertmanager_url"])
active_fps = {alert.get("fingerprint", "") for alert in alerts if alert.get("fingerprint")}
seen = seen.intersection(active_fps)
new_alerts = []
for alert in alerts:
    fp = alert.get("fingerprint", "")
    if fp and fp in seen:
        continue
    if fp:
        seen.add(fp)
    new_alerts.append(alert)
```

### 5. `verify.py` — chuẩn hóa điều kiện `up_ok` theo đúng spec

`baseline.json` ghi rõ `up` dùng `verify_pass_condition: "metric == threshold"`. Bản đầu dùng `up >= thresholds["up_required"]` (hành vi thực tế giống nhau vì `up` chỉ nhận giá trị 0 hoặc 1, nhưng không khớp đặc tả). Đã sửa thành:

```python
up_ok = up is not None and up == thresholds["up_required"]
```

---

## Điều học được

1. **Consecutive passes chống false positive:** Yêu cầu 3 mẫu liên tiếp pass mới kết luận recovery, tránh trường hợp container vừa bật lên đã báo "healthy" nhầm trong khi chưa chịu tải thật.
2. **Self-healing loop cần test bằng log thật, không chỉ thiết kế trên giấy:** Lần review trước, 3 stress scenario (S1/S2/S3) chỉ tồn tại trong `DESIGN.md` mà chưa từng chạy — sau khi chạy thật, phát hiện được các chi tiết nhỏ (ví dụ DRY-RUN của `multi_step_deploy.sh` không nhận step flag vẫn pass "chung", cần aware khi đọc log) mà thiết kế trên giấy không lường trước.
3. **Per-service mutex bảo vệ khỏi thundering herd:** Xử lý alert bất đồng bộ theo thread cần mutex riêng từng service — khác service luôn chạy song song, cùng service phải skip (không queue) để tránh restart trùng lặp không kiểm tra kết quả lần trước.
4. **Validation trước khi act ngăn lỗi cấu hình/hallucination leo lên circuit breaker:** Tách biệt rõ "lỗi quyết định" (decision validation fail) với "lỗi hành động" (action/verify fail) giúp circuit breaker không bị mở oan vì nguyên nhân không liên quan đến sức khỏe service thật.
5. **Backup/restore config khi test là kỷ luật cần có:** Đặc biệt với baseline thresholds dùng để ép fail có chủ đích — quên revert sẽ khiến hệ thống production sai lệch ngưỡng healthy thật.
