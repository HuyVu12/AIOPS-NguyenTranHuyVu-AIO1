# Báo cáo nộp bài (SUBMIT — W2-D2: Root Cause Analysis)

Dưới đây là câu trả lời chi tiết cho 3 câu hỏi EOD Checkpoint dựa trên kết quả triển khai và chạy thực tế hệ thống RCA của tôi:

---

## Câu 1: Đánh giá độ tự tin (Confidence) & Ngưỡng tự động xử lý sự cố (Auto-remediation threshold)

- **Độ tin cậy của top-1 trong cluster lớn nhất (`c-000-000`)**: Kết quả chạy thực tế trả ra độ tự tin cho `payment-svc` là **`0.549`**.
- **Ngưỡng thiết lập để tự động Rollback (Auto-rollback without SRE)**: Nếu phải cấu hình ngưỡng để hệ thống tự động chạy lệnh rollback phiên bản mà không cần SRE xác nhận, tôi sẽ chọn ngưỡng **`0.80`** (hoặc cao hơn, khoảng `0.85`).
- **Lý do**: 
  - Điểm số `0.549` của `payment-svc` là một điểm số "giằng co" (conflict). Trên khía cạnh thời gian (temporal), `payment-svc` là culprit thực tế và cảnh báo sớm nhất (temporal score = `1.0`). Nhưng trên khía cạnh PageRank đồ thị đảo ngược, do nó là lá (leaf/callee) ở cuối chuỗi cascade, điểm centrality của nó rất thấp (`0.249`), nhường điểm cao nhất cho các nạn nhân ở tầng trên như `checkout-svc` (`0.769`) và `edge-lb` (`0.728`).
  - Phép tính cụ thể:
    $$\text{combined\_score} = 0.6 \times \text{pagerank\_norm} + 0.4 \times \text{timestamp\_score}$$
    $$\text{combined\_score(payment-svc)} = 0.6 \times 0.249 + 0.4 \times 1.0 = 0.149 + 0.400 = 0.549$$
  - Điểm số combined thấp phản ánh cấu trúc đồ thị và thời gian không đồng thuận tuyệt đối (nguyên nhân gốc ở sâu nhưng ảnh hưởng dồn lên trên). Nếu chúng ta tự động chạy rollback với ngưỡng thấp như `0.50` hay `0.60`, hệ thống có thể rollback nhầm dịch vụ khi gặp các ca nhiễu hoặc các vụ tấn công ngoài ý muốn (ví dụ như nhầm lẫn giữa lỗi pool DB và lỗi DDoS ở cluster `c-005-000`). Mức ngưỡng `0.80` đảm bảo cả hai yếu tố: topo mạng và thời gian cảnh báo phát hỏa phải đồng thuận rất cao thì mới được kích hoạt hành động phá hủy/thay đổi trạng thái như rollback.

---

## Câu 2: Lựa chọn Variant cho Classifier & Đánh giá thực tế

- **Variant đã chọn**: **Variant A (Rule-based / Retrieval-based)** nâng cao sử dụng phương pháp biểu diễn văn bản **TF-IDF + Cosine Similarity** (Bonus 2) để so khớp cluster cảnh báo với tập lịch sử 30 sự cố.
- **Kết quả chạy thực tế**: 
  - Thuật toán chạy cực kỳ nhanh (thời gian xử lý dưới 10ms), hoàn toàn chạy offline và không phụ thuộc mạng/API key bên ngoài.
  - Kết quả so khớp rất chuẩn xác: Cluster `c-000-000` được ánh xạ chính xác về sự cố cạn kiệt pool DB của payment-svc (`INC-2025-11-08`), trong khi Cluster `c-005-000` được ánh xạ đúng về vụ DDoS tại edge-lb (`INC-2026-03-20`) dù cả hai đều chứa sự tham gia của `payment-svc` và `edge-lb`.
- **Trade-off (So sánh với Variant B / C sử dụng LLM)**:
  - **Điểm mạnh của Variant A**: Chắc chắn, deterministic (cùng đầu vào luôn ra cùng đầu ra), không tốn chi phí vận hành (0 USD API cost), tốc độ phản hồi cực nhanh (phù hợp cho xử lý sự cố thời gian thực) và tuyệt đối bảo mật (không gửi dữ liệu ra ngoài).
  - **Điểm yếu so với LLM**: Không có khả năng suy luận ngữ nghĩa linh hoạt (reasoning) đối với các sự cố mới tinh chưa từng xuất hiện trong tập lịch sử. Nếu có một lỗi lạ kết hợp giữa các dịch vụ mới, Variant A sẽ đưa ra gợi ý so khớp kém chính xác hoặc phải fallback về đồ thị thô, trong khi LLM có thể đọc hiểu kiến trúc và tự suy luận ra nguyên nhân một cách thông minh hơn.

---

## Câu 3: Định vị trên bản đồ công nghệ Industry & Tính phù hợp cho GeekShop

- **Sự tương đồng công nghệ**: Pipeline tự xây dựng này tương đồng nhất với kiến trúc **Dynatrace Davis**.
- **Lý do**:
  - Triết lý cốt lõi của Dynatrace Davis là coi topology mạng (Smartscape) là nguồn chân lý tối thượng để cô lập cascade chain. Sau đó, nó áp dụng các thuật toán duyệt đồ thị và tương quan thời gian thực để tìm ra dịch vụ gốc phát hỏa đầu tiên. Pipeline của chúng ta cũng xây dựng đồ thị con từ `services.json`, chạy PageRank trên đồ thị đảo ngược và kết hợp tuyến tính với thời gian cảnh báo.
- **Đánh giá tính hợp lý cho domain GeekShop**:
  - Lựa chọn này là **rất hợp lý** cho GeekShop hiện tại.
  - **Đặc thù GeekShop**: Là hệ thống thương mại điện tử (e-commerce), số lượng dịch vụ vừa phải (10 services + 4 stores) nhưng lượng truy cập và khối lượng cảnh báo phát sinh khi có sự cố rất lớn (alert volume cao), đồng thời bản đồ dịch vụ (service map) tương đối cố định và ít thay đổi động.
  - Trong domain này, việc duy trì một service graph chuẩn và tin cậy là khả thi. Do đó, phương pháp dựa trên topo (như Dynatrace Davis) sẽ chạy cực kỳ nhanh (phản hồi < 1 giây), khoanh vùng chính xác chuỗi lỗi lan truyền từ payment lên checkout mà không phải chịu sự tốn kém và độ trễ cao của việc học causal graph động từ time-series (như Causely) hoặc sự phức tạp cấu hình định tuyến của Prometheus AlertManager.
  - **Điểm yếu / Hạn chế**: Điểm yếu lớn nhất của cách tiếp cận này là sự phụ thuộc vào bản đồ kiến trúc tĩnh (`services.json`). Khi GeekShop phát triển thêm dịch vụ mới hoặc tái cấu trúc (ví dụ: tách `checkout-svc` thành `checkout-v2` và `coupon-svc`), nếu bản đồ `services.json` không được cập nhật thủ công kịp thời, hệ thống RCA sẽ bị stale và phân tích sai lệch hoàn toàn. 
  - *Mitigation*: Để giảm thiểu, chúng ta cần tự động đồng bộ sơ đồ topology mạng hàng ngày từ OpenTelemetry Service Map hoặc Kubernetes Service Mesh thay vì khai báo tĩnh bằng file JSON.
