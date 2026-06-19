# W3-D3 Submission — Nguyen Tran Huy Vu

## Outage chosen
- ID: 3
- Name: Cloudflare WAF regex 2019-07-02
- Why this one: I wanted to understand how a single regex backtracking vulnerability in a hot path can escalate into a global outage, and how we can implement safety guardrails to detect and mitigate CPU-pinning events.
- Failure mode: regex (Catastrophic Backtracking)

## 3 thứ tôi học từ outage này
1. **Catastrophic Backtracking nguy hiểm như thế nào:** Lỗi regex có các nhóm lượng từ xếp lồng nhau (nested quantifiers) khi so khớp với chuỗi không khớp (mismatch) sẽ dẫn đến số lượng phép thử tăng theo cấp số mũ. Điều này làm nghẽn CPU 100% gần như lập tức trên tất cả các core.
2. **Tầm quan trọng của Canary Rollout:** Triển khai một cấu hình WAF hay luật bảo mật toàn cầu cùng một lúc (global atomic deploy) sẽ xóa bỏ hoàn toàn vùng đệm an toàn. Cần phải áp dụng cơ chế cuộn dần (canary) từ 1% -> 10% -> 100%.
3. **Cơ chế giới hạn thời gian (Guardrail Timeouts):** Các hàm xử lý regex trên luồng xử lý chính (hot path) luôn luôn phải có giới hạn thời gian thực thi (timeout) hoặc cơ chế ngắt mạch (circuit breaker) để tránh việc khóa tài nguyên vô thời hạn.

## 1 thứ pipeline của tôi sẽ vẫn miss nếu outage này xảy ra real
- **Pattern:** Lỗi rò rỉ hiệu năng từ từ do độ dài chuỗi đầu vào tăng dần qua nhiều tuần (Slow-burn CPU pegging).
- **Why miss:** Bộ phát hiện bất thường dựa trên MWMAD (Moving Window Median Absolute Deviation) sử dụng cửa sổ động để cập nhật baseline. Nếu độ trễ và tải CPU tăng rất chậm qua nhiều tuần, baseline sẽ tự động thích nghi dần và coi mức tải cao đó là bình thường (normal baseline drift), từ đó bỏ sót sự cố.
- **Mitigation idea:** Kết hợp bộ so sánh baseline tĩnh dài hạn (ví dụ: so sánh với cấu hình sạch của 30 ngày trước) và tích hợp thêm kiểm tra kiểm thử tĩnh luật regex trong CI/CD trước khi deploy.

## 1 quyết định trong ADR mà tôi không hoàn toàn chắc
- Quyết định sử dụng thuật toán **Granger Causality** (quan hệ nhân quả Granger) trên tập metrics động. Granger Causality giả định mối quan hệ tuyến tính giữa các chuỗi thời gian và đòi hỏi chi phí tính toán rất cao ($O(N \times \text{lag\_window})$). Khi xảy ra alert storm lớn với hàng nghìn alert đồng thời, tính toán này có thể làm quá tải chính hệ thống AIOps, gây trễ cảnh báo.

## Cost model verdict cho stack của tôi
- **ROI:** 1.44
- **Payback:** 0.69 tháng (khoảng 21 ngày)
- **Verdict:** marginal (hiệu quả ở mức trung bình/biên giới hạn, cần cân nhắc tối ưu thêm chi phí kỹ sư vận hành hệ thống).
