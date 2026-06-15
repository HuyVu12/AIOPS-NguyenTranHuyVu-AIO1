# W3-D1 Submission — Huy Vu

## 3 thứ tôi học được
1. **Multi-Window Multi-Burn-Rate (MWMBR) Alerts**: Cách kết hợp các cửa sổ thời gian dài và ngắn (ví dụ: 1h và 5m) để giải quyết triệt để sự đánh đổi giữa tốc độ phát hiện sự cố (detection speed) và độ nhiễu (noise/reset time). Việc này giúp loại bỏ hoàn toàn các cảnh báo ảo (False Positives) từ biến động ngẫu nhiên trong khi vẫn nhanh chóng bắt được các lỗi nghiêm trọng.
2. **Loại trừ lỗi 4xx client-side khỏi SLI**: Hiểu rõ lý do vì sao cần phải loại trừ các lỗi 4xx (ngoại trừ 429 - rate limit) ra khỏi công thức tính toán độ tin cậy. Dữ liệu thực tế cho thấy các lỗi này chiếm khoảng 2.0% trên mọi endpoint và phản ánh hành vi của client chứ không phải lỗi hệ thống. Nếu gộp vào, chúng sẽ làm cạn kiệt Error Budget của SLO 99.9%.
3. **Ý nghĩa của Error Budget**: Error budget là thước đo liên kết giữa tốc độ phát triển và sự ổn định hệ thống. Cần phải tính toán downtime tương đương dựa trên lưu lượng thực tế (`baseline.json`) để đưa ra các SLO thực tế.

## 1 thứ vẫn chưa rõ
- Với MWMBR alerts, làm thế nào để tích hợp tối ưu vào Alertmanager và tránh lặp tin nhắn giữa các công cụ chat (Slack/Teams) và hệ thống on-call trực tiếp (PagerDuty/Opsgenie), đặc biệt khi cần phân loại mức độ nghiêm trọng khác nhau (`page` cho Tier 1/2 và `ticket` cho Tier 3).

## 1 trade-off trong SLO decision của tôi mà tôi không chắc
- Việc thiết lập SLO của DB là **99.95%** (cho phép downtime ~22 phút/tháng). Với một hệ thống e-commerce quy mô nhỏ (peak ~15 req/s), đây là một mục tiêu khá cao và có thể gây áp lực không cần thiết cho đội ngũ vận hành DB, đặc biệt khi chưa cấu hình failover tự động hoàn toàn hoặc các cơ chế cache mạnh ở tầng API. Tuy nhiên, nếu hạ xuống 99.9% thì có thể không phản ánh đúng tính chất "tĩnh" và ít lỗi của DB so với API.

## Validation report
- noise_reduction_pct: 86.4%
- mttd_delta_s: 60s
- false_negative: 0
- verdict: pass
