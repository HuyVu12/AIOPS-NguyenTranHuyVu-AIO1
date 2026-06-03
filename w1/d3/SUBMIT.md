# Week 01 — Nộp bài: Nền tảng AIOps Observability

**Ngày:** 2026-06-03  
**Tác giả:** Platform Engineering  
**Phạm vi:** Phase 2 (Kiến trúc) · Phase 2b (Ước tính chi phí) · Phase 3 (ADR) · Phase 4 (Reflection)

---

## 1. Sơ đồ Kiến trúc
![alt text](<E2E Data Layer Architecture for Anomaly Detection in Payment Services.drawio.png>)

## 2. Bảng Ước tính Chi phí

Số liệu từ `cost_model.py`

### Tier Small — 10 services, 50 GB/ngày, 100K events/giây

**Chi phí tự build (Self-hosted):**

| Component        | Compute  | Storage  | Network  | Tổng     |
|------------------|----------|----------|----------|----------|
| OTel Collector   | $61      | $2       | $0       | $62      |
| Apache Kafka     | $420     | $30      | $0       | $450     |
| Apache Flink     | $420     | $12      | $0       | $432     |
| VictoriaMetrics  | $140     | $57      | $0       | $197     |
| Loki             | $140     | $4       | $1       | $145     |
| Grafana          | $61      | $2       | $0       | $63      |
| **TỔNG**         | **$1,243** | **$105** | **$2** | **$1,350** |

**Datadog SaaS:** $3,380/tháng → **đắt hơn 2.5 lần** → Tiết kiệm/năm: **$24K**

---

### Tier Medium — 100 services, 500 GB/ngày, 1M events/giây

**Chi phí tự build (Self-hosted):**

| Component        | Compute  | Storage  | Network  | Tổng     |
|------------------|----------|----------|----------|----------|
| OTel Collector   | $304     | $8       | $0       | $312     |
| Apache Kafka     | $420     | $300     | $0       | $720     |
| Apache Flink     | $701     | $12      | $0       | $713     |
| VictoriaMetrics  | $280     | $565     | $0       | $845     |
| Loki             | $140     | $37      | $14      | $191     |
| Grafana          | $61      | $2       | $4       | $67      |
| **TỔNG**         | **$1,906** | **$924** | **$18** | **$2,848** |

**Datadog SaaS:** $33,800/tháng → **đắt hơn 11.9 lần** → Tiết kiệm/năm: **$371K**

---

### Tier Large — 1000 services, 5 TB/ngày, 10M events/giây

**Chi phí tự build (Self-hosted):**

| Component        | Compute    | Storage   | Network  | Tổng       |
|------------------|------------|-----------|----------|------------|
| OTel Collector   | $3,037     | $80       | $0       | $3,117     |
| Apache Kafka     | $1,682     | $3,072    | $0       | $4,754     |
| Apache Flink     | $11,353    | $84       | $0       | $11,437    |
| VictoriaMetrics  | $2,803     | $5,651    | $0       | $8,454     |
| Loki             | $280       | $378      | $138     | $796       |
| Grafana          | $121       | $3        | $45      | $170       |
| **TỔNG**         | **$19,277** | **$9,268** | **$183** | **$28,728** |

**Datadog SaaS:** $344,480/tháng → **đắt hơn 12 lần** → Tiết kiệm/năm: **$3.79M**

---

### Tổng hợp tất cả Tier

| Tier   | Self-hosted/tháng | Datadog/tháng | Tỉ lệ   | Tiết kiệm/năm |
|--------|-------------------|---------------|---------|---------------|
| Small  | $1,350            | $3,380        | 2.5x    | $24,361       |
| Medium | $2,848            | $33,800       | 11.9x   | $371,427      |
| Large  | $28,728           | $344,480      | 12.0x   | $3,789,030    |

> 💡 **Insight quan trọng:** Chi phí Datadog tăng **tuyến tính** theo lượng data (tính phí $0.10/GB ingest).
> Chi phí self-hosted tăng **dưới tuyến tính** — khi data tăng 100x (Small → Large), infra chỉ tốn thêm 21x,
> trong khi Datadog tốn thêm 102x.

---

## 3. Tóm tắt ADR — ADR-001: Apache Kafka làm Transport Layer thay vì Direct Push

**Quyết định:** Tích hợp **Apache Kafka** làm tầng vận chuyển trung gian đứng giữa OpenTelemetry Collector và Apache Flink, thay vì để OTel Collector đẩy trực tiếp (Direct Push) vào Flink qua gRPC/HTTP.

---

**Vấn đề cần giải quyết:**

Core Payment Service xử lý **5,000 RPS trung bình**, đỉnh điểm tới **25,000 RPS** (ngày lễ, giờ vàng). Mỗi giao dịch sinh ra trung bình 3 bản ghi telemetry → tải đầu vào dao động từ **15,000 đến 75,000 events/giây**. Với mô hình Direct Push, khi Flink bị quá tải hoặc ML Inference phản hồi chậm (>200ms), backpressure dội ngược về OTel Collector. Do OTel chỉ có in-memory buffer ~100MB, **data giao dịch sẽ bị drop ngay lập tức** khi tràn bộ đệm.

---

**Lý do chọn Kafka:**

| Tiêu chí | Direct Push | Kafka Transport |
|---|---|---|
| Độ trễ E2E (p99) | ≤5ms (lý tưởng) | ~25ms (+10–20ms overhead) |
| An toàn dữ liệu khi Flink sập | ❌ Mất vĩnh viễn | ✅ Lưu trên disk tới 24h |
| Chịu tải peak 75K events/s | ❌ Drop khi tràn buffer | ✅ Kafka partition scale-out |
| Buffering khi bảo trì 2 tiếng | ❌ Không thể | ✅ 540 triệu events lưu an toàn |
| Chi phí hạ tầng | $0 thêm | ~$180–$220/tháng (3-node cluster) |

**Đánh đổi chấp nhận:** thêm ~$200/tháng và 25ms độ trễ để đổi lấy **zero data loss** — hoàn toàn xứng đáng với use case Payment Anomaly Detection, nơi mất event đồng nghĩa mất bằng chứng gian lận.

---

**Lựa chọn đã loại bỏ:**

- **Direct Push (OTel → gRPC → Flink):** Đơn giản nhưng không có replay capability, mất data khi Flink quá tải
- **RabbitMQ:** Xóa message ngay sau ACK, không tối ưu cho stream throughput cao; nghẽn RAM khi hàng triệu message tồn đọng

Chi tiết đầy đủ: [ADR-001.md](./ADR-001.md)

---

## 4. Reflection: Build hay Buy cho Startup Series A 50 Services?

### Bối cảnh

Startup vừa raise Series A, đang vận hành **50 services**, team nhỏ, áp lực ship feature liên tục.
Câu hỏi: Nên tự build observability platform hay dùng Datadog?

---

### Khuyến nghị: **Mua Datadog trước, lên kế hoạch tự build khi vượt 100 services**

Nếu được hire làm Platform Engineer cho startup này, tôi sẽ **chọn Datadog ở giai đoạn Series A** và
đặt trigger rõ ràng để migrate. Đây là lý do:

---

**1. Tốc độ engineering quan trọng hơn chi phí hạ tầng ở giai đoạn này**

Ở 50 services, Datadog tốn khoảng **$7K–10K/tháng** — không rẻ, nhưng đổi lại mua được **6 tuần
engineering time** và **1.5 FTE ops work** mà startup Series A không có. Mỗi engineer-week dành để
tune Kafka hay debug Flink rebalance là một tuần không ship product feature.

---

**2. Chi phí chênh lệch chấp nhận được — cho đến khi không chấp nhận được nữa**

Ở 50 services, Datadog đắt hơn self-hosted khoảng **$5K–8K/tháng**. Post Series A, con số đó
thường chưa đến 2% burn rate. Chi phí ẩn của self-hosted là **1.5 FTE × $120K/năm = $180K/năm**
tiền lương SRE. Bài toán chỉ lật ngược rõ ràng khi vượt 100 services và có đội Platform riêng.

---

**3. Dùng Datadog để học được nhu cầu observability thực tế**

Ở giai đoạn đầu, chưa biết cardinality shape, log pattern, threshold cảnh báo nào phù hợp.
Datadog cho instant dashboard và ML anomaly detection trong lúc team tập trung vào product.
Khoảng thời gian 6–18 tháng này để **hiểu đúng nhu cầu**, không phải đoán mò từ đầu.

---

**4. Đặt trigger migration rõ ràng — không để "drift" mãi dùng Datadog**

Tôi sẽ đề xuất chính sách cụ thể: **khi Datadog spend vượt $25K/tháng** (khoảng 100–150 services),
bắt đầu dự án 6 tuần migrate sang self-hosted stack. Stack OTel → Kafka → Flink → Loki/VictoriaMetrics
→ Grafana đã được battle-test tại Cloudflare, Grab, Shopify — không còn là thí nghiệm nữa.

---

**5. Trick quan trọng nhất: dùng OTel SDK từ ngày đầu**

Instrument tất cả services bằng **OpenTelemetry SDK ngay từ đầu**, nhưng trỏ OTel Collector
output vào Datadog OTLP endpoint. Khi quyết định migrate, chỉ cần đổi config OTel Collector
sang self-hosted backend — **không cần re-instrument một dòng code nào**. Đây là nước đi
thông minh nhất để giữ tùy chọn mở.

---

### Bảng quyết định

| Tiêu chí                    | Series A (50 svcs) | Series B+ (150+ svcs)    |
|-----------------------------|--------------------|-----------------------------|
| Chi phí Datadog/tháng       | ~$8K               | ~$50K+                      |
| Chi phí self-hosted/tháng   | ~$2K (infra)       | ~$5K (infra)                |
| Chi phí SRE labor/tháng     | $15K (1.5 FTE)     | $20K (2 FTE, đã có headroom)|
| Engineering time tiết kiệm  | Cao                | Vừa                         |
| Yêu cầu compliance          | Thường thấp        | Thường bắt buộc             |
| **Khuyến nghị**             | **Mua (Datadog)**  | **Tự build (self-hosted)**  |

---

### Kết luận

> **"Mua để đi nhanh, tự build để tiết kiệm chi phí."**
>
> Dùng Datadog ở Series A để giữ tốc độ engineering. Migrate sang open-source stack khi observability
> spend vượt **$25K/tháng**, hoặc khi compliance yêu cầu data sovereignty.
> Migration chỉ mất 6 tuần — không phải lý do để đánh đổi tốc độ giai đoạn đầu.
>
> Bước quan trọng nhất ngay bây giờ: **instrument bằng OTel SDK, đừng dùng Datadog agent native.**
> Đó là quyết định giúp giữ tùy chọn mở mà không tốn thêm chi phí hay effort nào.

---

## Danh sách file nộp bài

| File              | Mô tả                                                                  |
|-------------------|------------------------------------------------------------------------|
| `architecture.md` | Thiết kế kiến trúc Data Layer E2E (Phase 2)                            |
| `pipeline.py`     | Pipeline Producer/Consumer với feature engineering (rolling stats)     |
| `cost_model.py`   | Ước tính chi phí 3 tier × self-hosted vs Datadog; in bảng so sánh      |
| `ADR-001.md`      | Architecture Decision Record: Kafka+Flink vs Datadog (tiếng Việt)     |
| `SUBMIT.md`       | File này — sơ đồ, bảng chi phí, tóm tắt ADR, reflection               |
