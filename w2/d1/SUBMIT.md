# SUBMIT — W2-D1: Alert Correlation

## Tham số đã chọn

**gap_sec = 30** — Với dataset 20 alert trải dài ~6 phút (09:42–09:48), gap 30 giây tự nhiên tách được các burst khác nhau. Nhìn vào timestamp: alert a-0001 đến a-0010 cách nhau ≤ 30s liên tục → 1 session. Sau đó a-0012 (09:44:30) cách a-0010 (09:43:50) là 40s → session mới. gap_sec=120 gom quá nhiều (1 cluster), gap_sec=30 ra 6 clusters với ratio=0.70 vẫn đạt ngưỡng ≥ 0.5.

**max_hop = 2** — Cho phép gom các service có quan hệ gián tiếp 1 bước. Ví dụ: edge-lb → checkout-svc → payment-svc là 2 hop, gom chúng lại đúng vì cascade thực sự. Nếu max_hop=1 thì chỉ gom service trực tiếp gọi nhau, sẽ bỏ sót cascade dài hơn.

---

## EOD Checkpoint

### Câu 1 — Vì sao fingerprint không include timestamp hay value?

Nếu include `timestamp` hoặc `value`, mỗi lần alert fire ra 1 fingerprint khác nhau hoàn toàn — dedup trở nên vô nghĩa. Ví dụ cụ thể: `payment-svc|latency_p99_ms|crit` fire 3 lần (a-0003, a-0008, a-0015) với `ts` và `value` giống hệt nhau (1840ms) nhưng timestamp khác nhau. Nếu include timestamp, cả 3 sẽ là 3 fingerprint riêng biệt → dedup không gom được → tạo 3 cluster thay vì 1, làm tăng noise thay vì giảm.

### Câu 2 — Sự khác biệt giữa "duplicate" và "correlated" alert

**Duplicate**: cùng service, cùng metric, cùng severity — fire đi fire lại. Ví dụ: a-0003, a-0008, a-0015 đều là `payment-svc|latency_p99_ms|crit` với value 1840ms — đây là 1 alert bắn lại 3 lần.

**Correlated**: các alert khác nhau (service hoặc metric khác) nhưng cùng 1 nguyên nhân gốc. Ví dụ: a-0001 (`payment-svc|db_connection_pool_used_ratio|warn`), a-0003 (`payment-svc|latency_p99_ms|crit`), a-0006 (`checkout-svc|downstream_payment_error_rate|crit`) — 3 fingerprint khác nhau nhưng đều là hệ quả của việc payment-svc bị pool exhaustion. Dedup không gom được chúng, phải dùng time-window + topology.

### Câu 3 — gap_sec = 30 vs gap_sec = 600

- **gap_sec=30**: Ra 6 clusters nhỏ, tách tốt các burst riêng biệt. Rủi ro: incident kéo dài > 30s giữa 2 alert liên tiếp sẽ bị cắt thành 2 cluster dù cùng cause.
- **gap_sec=600**: Ra 1 cluster khổng lồ gom toàn bộ 17 alert vào 1 nhóm. Đơn giản hoá quá mức — không phân biệt được sự kiện không liên quan xảy ra trong cùng 10 phút.

### Câu 4 — Recommender-svc có bị gom vào cluster chính không?

**Không** — với `gap_sec=30`, correlator tách `recommender-svc|cpu_utilization|warn` (a-0013, 09:45:10) thành cluster riêng (`c-002-000`). Lý do: a-0012 (09:44:30) cách a-0013 (09:45:10) là 40 giây > 30s → tạo session mới. Recommender chỉ có 1 alert trong session đó → topology group riêng.

Đây là kết quả đúng. Recommender alert là do batch retrain — không liên quan đến payment-svc pool exhaustion. Nhờ time-window tách session, correlator không gom nhầm. Nếu dùng `gap_sec=120`, alert này sẽ bị gom nhầm vào cluster chính vì nằm trong cùng time window — false correlation.

### Câu 5 — Limitation của topology grouping và hướng khắc phục

**Limitation**: Topology grouping dùng `max_hop` cố định trên undirected graph — không phân biệt chiều phụ thuộc. Kết quả là `search-svc` và `checkout-svc` bị gom chung (c-004-000) vì cả hai cách nhau 2 hop qua `catalog-db`, dù a-0016 (`search-svc|catalog_db_query_time_ms|warn`) được label rõ là "noise — independent slow query". Topology không biết alert nào là cause, alert nào là noise — nó chỉ biết "gần nhau trên graph".

**Khắc phục**: Thêm **criticality weighting** — service `critical` (payment-svc) là anchor của cluster; chỉ kéo vào những service nằm trên call path thực sự đến anchor (dùng directed graph thay vì undirected, chỉ xét upstream callers). Service không nằm trên path đến bất kỳ `critical` service nào trong cluster thì không được gom dù gần về hop count.
