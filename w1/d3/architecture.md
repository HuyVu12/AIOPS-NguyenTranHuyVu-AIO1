# E2E Data Layer Architecture for Anomaly Detection in Payment Services
### 1. Sơ đồ Kiến trúc Tổng quan (Data Layer Topology)
![alt text](<E2E Data Layer Architecture for Anomaly Detection in Payment Services.drawio.png>)
## 2. Chi tiết các Thành phần Hệ thống (Component Details)

### 2.1. Service Layer
* **Thành phần cụ thể:** Core Payment Service.
* **Công nghệ sử dụng:** Java Spring Boot hoặc Golang.
* **Vai trò:** Đảm nhận toàn bộ logic giao dịch chuyển tiền, thanh toán hóa đơn. Đây là nơi phát sinh các điểm dữ liệu thô (giao dịch, thời gian phản hồi, trạng thái thành công/thất bại).
* **Rủi ro cần giám sát:** Đột biến lượng request (DDoS), lỗi hệ thống bên thứ 3 (mã lỗi 5xx), hoặc hành vi gian lận (giao dịch số tiền lớn bất thường trong khoảng thời gian ngắn).

### 2.2. Collection Layer
* **Thành phần cụ thể:** OpenTelemetry (OTel) SDK & OpenTelemetry Collector.
* **Vai trò:** * **OTel SDK:** Được tích hợp trực tiếp dưới dạng middleware hoặc agent trong mã nguồn của Payment Service nhằm tự động capture các metric hạ tầng và metric nghiệp vụ (`http.status_code`, `latency`, `transaction_amount`).
    * **OTel Collector:** Tiếp nhận dữ liệu từ nhiều instance của Payment Service qua giao thức OTLP chất lượng cao, thực hiện gom cụm (batching), chuẩn hóa cấu trúc dữ liệu trước khi đẩy ra tầng kế tiếp nhằm giảm thiểu tối đa overhead cho service chính.

### 2.3. Transport Layer
* **Thành phần cụ thể:** Apache Kafka.
* **Vai trò:** Đóng vai trò là hệ thống Message Broker phân tán, chịu lỗi cao (fault-tolerant). Toàn bộ luồng telemetry từ OTel Collector được publish vào Kafka topic `payment-telemetry`. Kafka giúp giải quyết bài toán **Backpressure** (áp lực ngược) khi hệ thống gặp tải cao đột biến, đảm bảo dữ liệu không bị mất mát trước khi chuyển tới tầng xử lý.

### 2.4. Processing Layer
* **Thành phần cụ thể:** Apache Flink liên kết với Machine Learning Inference Service.
* **Vai trò:** * **Apache Flink:** Trái tim của hệ thống xử lý stream thời gian thực. Flink khởi tạo các chu kỳ cửa sổ trượt (**Sliding Windows**) ngắn (ví dụ: window 1 phút, trượt mỗi 5 giây) để tính toán liên tục các đặc trưng động như: Tỷ lệ lỗi 5xx, Tốc độ thay đổi (Rate of change) của latency, Tổng số tiền giao dịch trên một tài khoản.
    * **ML Inference Service:** Flink stream từng bản ghi đặc trưng qua gRPC tới Service ML (chạy mô hình Isolation Forest hoặc các thuật toán phân cụm). Service này sẽ chấm điểm (Anomaly Score) và trả về nhãn `is_anomaly: true/false` trong vài mili-giây.

### 2.5. Storage Layer
* **Thành phần cụ thể:** VictoriaMetrics & Elasticsearch.
* **Vai trò:**
    * **VictoriaMetrics:** Lưu trữ toàn bộ dữ liệu dạng Time-Series (Metrics hệ thống, tỉ lệ anomaly theo thời gian). Lựa chọn VictoriaMetrics vì hiệu năng ghi cực cao, tốn ít tài nguyên lưu trữ hơn Prometheus truyền thống ở quy mô lớn.
    * **Elasticsearch:** Khi một giao dịch hoặc một chuỗi trace bị gắn cờ `is_anomaly: true`, toàn bộ thông tin chi tiết (Detailed Logs, Transaction Payload, Full Distributed Tracing) sẽ được Flink đẩy vào Elasticsearch để phục vụ công tác điều tra chuyên sâu (Deep Forensics) của đội ngũ SRE và bảo mật.

### 2.6. Query / ML / Alert Layer
* **Thành phần cụ thể:** Grafana & Grafana Alerting.
* **Vai trò:** * Tập hợp dữ liệu từ cả 2 nguồn VictoriaMetrics (để vẽ biểu đồ xu hướng, biểu đồ tỷ lệ lỗi) và Elasticsearch (để tra cứu nhanh các log chứa mã lỗi cụ thể).
    * Cấu hình **Grafana Alerting Engine**: Định nghĩa các rule cảnh báo thông minh (ví dụ: Nếu số lượng giao dịch bất thường vượt quá 5% tổng số giao dịch trong 3 phút liên tiếp, lập tức trigger alert). Hệ thống tự động gửi tin nhắn khẩn cấp kèm ID giao dịch lỗi qua Slack, Telegram hoặc PagerDuty cho đội trực On-call.

---

## 3. Luồng đi của dữ liệu (End-to-End Data Flow)

1.  **Kích hoạt sự kiện:** Người dùng thực hiện một giao dịch thanh toán trên ứng dụng di động ➔ Giao dịch đi qua **Payment Service**.
2.  **Thu thập dữ liệu:** **OpenTelemetry SDK** ghi nhận thời gian xử lý (latency) của giao dịch này là `1500ms` (bình thường là `50ms`) do hệ thống Gateway đối tác bị nghẽn.
3.  **Vận chuyển luồng:** Thông tin latency + log giao dịch được đẩy qua **OTel Collector** và lưu tạm thời vào partition tương ứng trong **Apache Kafka**.
4.  **Phân tích thời gian thực:** **Apache Flink** tiêu thụ event từ Kafka, nhận thấy biến động đột biến, kết hợp gọi sang **ML Model** nhận về kết quả đánh giá điểm số bất thường vượt ngưỡng an toàn.
5.  **Lưu trữ phân tách:** Flink ghi nhận metric bất thường vào **VictoriaMetrics**, đồng thời clone chi tiết trace log giao dịch đó sang **Elasticsearch**.
6.  **Cảnh báo tức thời:** **Grafana** phát hiện điểm dữ liệu mới trong VictoriaMetrics vượt ngưỡng đỏ thiết lập, kích hoạt webhook gửi thẳng thông báo về kênh Telegram của đội ngũ kĩ thuật xử lý sự cố.

