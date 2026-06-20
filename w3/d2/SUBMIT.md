# W3-D2 Submission — Nguyen Tran Huy Vu

## 3 things I learned about my AIOps pipeline
1. **Symptom vs. Root Cause Correlation:** Trong kịch bản retry storm (Experiment 10), tôi học được rằng nếu chỉ dùng phương pháp đếm số lượng alert đơn giản, hệ thống sẽ luôn gán nhãn sai cho service gánh chịu hậu quả (như `checkout-svc`) làm nguyên nhân gốc rễ. Nhờ topology-aware correlation, ta có thể bỏ qua "tiếng ồn" ở ngọn và đi sâu vào gốc của đường truyền phụ thuộc.
2. **Hạn chế của Moving Windows:** Việc lấy trung bình trượt (Moving Average) trong các khoảng thời gian dài (như 5 phút) vô tình triệt tiêu và làm mịn đi các lỗi trễ xuất hiện gián đoạn (intermittent latency) hoặc từ từ (slow-burn).
3. **Sự cần thiết của Meta-Monitoring:** Một hệ sinh thái AIOps không chỉ giám sát các ứng dụng kinh doanh mà còn phải giám sát chính các thành phần hạ tầng (như dung lượng ổ đĩa của bộ thu thập log `log-collector` ở Experiment 7), nếu không hệ thống sẽ rơi vào trạng thái "mù" thông tin khi hạ tầng này sập.

## 1 fault I expected the pipeline to catch but it missed
- **Experiment:** Experiment 7 (log-collector disk fill 95%).
- **Why I expected detection:** Tôi kỳ vọng pipeline sẽ phát hiện ra sự cố này vì khi ổ đĩa đầy 95%, hệ thống thu thập log chắc chắn sẽ bị nghẽn (ingestion lag tăng vọt), ảnh hưởng đến tốc độ lưu vết sự cố.
- **Why the pipeline missed (hypothesis):** Bộ giám sát của pipeline hiện tại chỉ cấu hình kéo các số liệu về HTTP request và CPU, hoàn toàn bỏ sót các số liệu về hạ tầng lưu trữ ổ đĩa (`node_filesystem_free_bytes`). Vì vậy hệ thống không có dữ liệu đầu vào để phát hiện bất thường.

## 1 trade-off in pipeline design I want to rethink
- Việc sử dụng cửa sổ thời gian tĩnh 120 giây (gap_sec=120) để gom nhóm alert. Đây là sự đánh đổi giữa **độ chính xác** và **tốc độ phát hiện**. 
  * Cửa sổ quá nhỏ sẽ chia tách một sự cố lớn thành nhiều sự cố nhỏ rời rạc (làm giảm hiệu năng RCA).
  * Cửa sổ quá lớn sẽ gộp nhầm các sự cố độc lập xảy ra gần nhau làm một (false positive).
  * Tôi muốn thiết kế lại cơ chế này thành cửa sổ động thích ứng (adaptive time window) dựa trên tốc độ truyền lan lỗi (propagation delay) giữa các dịch vụ trong sơ đồ topo.

## Scoreboard summary
- detected: 8/10
- rca_correct: 7/8
- mttd_p50: 22s
- false_alarms: 0
- verdict: pass (đạt yêu cầu detected >= 7/10, RCA correct >= 5/detected và False Alarms <= 1)
