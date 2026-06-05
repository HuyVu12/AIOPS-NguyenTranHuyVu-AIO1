# Detection Approach — DESIGN.md

## Approach dùng

**Z-score với Sliding Window** kết hợp **absolute threshold** cho early detection.

## Tại sao chọn approach này

Streaming data không có label và không có lịch sử dài để train model. Z-score sliding window phù hợp vì:
- Không cần training phase — tự tính baseline từ các datapoint gần nhất
- Thích nghi với diurnal pattern (traffic ban đêm thấp hơn ban ngày) vì baseline liên tục cập nhật
- Nhẹ về compute, độ trễ detection thấp (mỗi tick là O(n) với n = window size)
- Dễ tune threshold mà không cần re-deploy model

Absolute threshold bổ sung để bắt fault ngay cả khi pipeline chưa có đủ baseline (fault xảy ra sớm).

## Cách hoạt động

Mỗi khi nhận một datapoint:
1. Append giá trị metric vào sliding window (deque với `maxlen=30`)
2. Tính mean và standard deviation của window
3. Z-score = `(current_value - mean) / stdev`
4. Kiểm tra absolute threshold trước (không cần baseline), rồi z-score sau
5. Nếu vượt threshold → fire alert và ghi vào `alerts.jsonl`
6. Cooldown 5 ticks giữa các alert cùng type để tránh spam

### Mapping fault → signal chính

| Fault type | Signal chính | Signal phụ |
|---|---|---|
| `memory_leak` | `memory_usage_bytes / memory_limit_bytes` ≥ 70% + `jvm_gc_pause_ms_avg` z-score cao | Log `OutOfMemory`, `GC pause` |
| `traffic_spike` | `http_requests_per_sec` > 400 AND `http_p99_latency_ms` > 800 (absolute) | z-score khi có baseline |
| `dependency_timeout` | `upstream_timeout_rate` ≥ 15% OR (z-score cao AND 5xx > 5%) | Log `circuit breaker`, `timeout` |

## Parameters tôi chọn

| Parameter | Giá trị | Lý do |
|---|---|---|
| `WINDOW_SIZE` | 30 | ~5 phút real-time ở speed=10, đủ để ổn định baseline |
| `WARMUP_TICKS` | 20 | Tránh false alert khi window chưa đủ data |
| `ZSCORE_WARNING` | 2.5σ | ~1.2% false positive rate cho phân phối chuẩn |
| `ZSCORE_CRITICAL` | 4.0σ | ~0.006% false positive rate |
| `RPS_CRITICAL` | 400 req/s | ~3x normal max (~160), rõ ràng bất thường |
| `LAT_CRITICAL` | 800ms | ~12x normal (65ms), không thể nhầm với noise |
| `UPSTREAM_TIMEOUT_CRITICAL` | 15% | Normal range là 0–0.4%, 15% là bất thường rõ ràng |
| `ALERT_COOLDOWN_TICKS` | 5 | Tránh spam, giữ 1 alert mỗi ~2.5s real-time |

## Cải thiện nếu có thêm thời gian

- **EWMA** thay cho simple mean — phản ứng nhanh hơn với thay đổi gần đây
- **Isolation Forest** để detect anomaly đa biến, khai thác correlation giữa metrics
- Persistence state để pipeline restart không mất window history
- Phân biệt severity rõ hơn bằng cách track duration của fault
