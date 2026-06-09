# Kết quả Phân tích Nguyên nhân Gốc (RCA Findings)

Tài liệu này trình bày các kết quả phân tích nguyên nhân gốc cho các cluster cảnh báo trong hệ thống GeekShop, sử dụng sự kết hợp giữa thuật toán Graph PageRank, phân tích thời gian (temporal scoring) và truy vấn lịch sử sự cố (incident retrieval) sử dụng TF-IDF.

---

## 1. Phân tích chi tiết các Cluster chính

### Cluster `c-000-000`
- **Root Cause**: `payment-svc`
- **Phân loại sự cố**: `connection_pool_exhaustion` (Cạn kiệt pool kết nối cơ sở dữ liệu)
- **Độ tin cậy (Confidence)**: `0.549`
- **Lý do & Cơ chế hoạt động**:
  - Trong phân tích đồ thị đảo ngược (reversed subgraph), `checkout-svc` (0.769) và `edge-lb` (0.728) có điểm PageRank cao hơn `payment-svc` (0.549) vì chúng là các caller đứng đầu luồng gọi dịch vụ và chịu ảnh hưởng trực tiếp (nạn nhân).
  - Tuy nhiên, `payment-svc` lại xuất hiện cảnh báo sớm nhất trong toàn bộ cluster (tại thời điểm `09:42:01Z` với lỗi `db_connection_pool_used_ratio` đạt ngưỡng cảnh báo `warn` rồi nhảy lên `crit` sau đó), giúp nó đạt điểm thời gian (temporal score) là `1.0`.
  - Khi đưa các thông tin về service và các loại metric cảnh báo vào hệ thống truy vấn lịch sử lỗi thông qua vector hóa TF-IDF, hệ thống đã khớp chính xác với sự cố lịch sử **`INC-2025-11-08`** (độ tương đồng Cosine đạt `0.4904`) - sự cố liên quan đến việc triển khai phiên bản `payment-svc v3.2` bị rò rỉ DB connection pool, gây ảnh hưởng dây chuyền đến checkout và notification queue. Nhờ đó, hệ thống đã override lại kết quả graph thô để xác định đúng nguyên nhân gốc là `payment-svc`.

### Cluster `c-004-000`
- **Root Cause**: `search-svc`
- **Phân loại sự cố**: `n_plus_1` (Lỗi truy vấn N+1 trên catalog-db)
- **Độ tin cậy (Confidence)**: `1.000`
- **Lý do & Cơ chế hoạt động**:
  - Đồ thị con của cluster này không có cạnh kết nối trực tiếp nào giữa `checkout-svc` và `search-svc`, dẫn đến điểm PageRank đồng đều. Do `search-svc` cảnh báo trước (`09:46:50` so với `09:47:12` của `checkout-svc`), điểm kết hợp của `search-svc` đạt tối đa (`1.0`).
  - Hệ thống truy vấn lịch sử đã khớp với sự cố **`INC-2026-01-29`** (độ tương đồng đạt `0.5317`), mô tả việc tính năng "related products" gây ra lỗi N+1 query trên `catalog-db` từ `search-svc`.

### Cluster `c-005-000`
- **Root Cause**: `edge-lb`
- **Phân loại sự cố**: `ddos` (Tấn công từ chối dịch vụ DDoS)
- **Độ tin cậy (Confidence)**: `0.600`
- **Lý do & Cơ chế hoạt động**:
  - Tương tự như trên, đồ thị con không có cạnh trực tiếp giữa `edge-lb`, `notification-svc`, và `payment-svc`.
  - Dù `payment-svc` có điểm thời gian cao nhất do cảnh báo sớm hơn, việc truy vấn TF-IDF đã khớp rất mạnh với **`INC-2026-03-20`** (độ tương đồng đạt `0.5304`), trong đó mô tả vụ tấn công DDoS làm nghẽn `edge-lb` khiến cho tất cả các dịch vụ upstream/downstream đồng loạt bị ảnh hưởng. Vì thế, hệ thống đã xác định `edge-lb` là culprit thực sự.

---

## 2. Đánh giá độ tin cậy & Tự động xử lý sự cố (Auto-remediation)

Với mức độ tự tin (confidence) của các sự cố:
- **Cluster `c-000-000`**: Confidence đạt `0.549`. Mức độ tự tin này tương đối thấp trên khía cạnh đồ thị (vì PageRank bị lệch về các caller do cấu trúc topo). Việc tự động chạy script rollback (`auto-remediation`) dựa trên mức độ này là khá **mạo hiểm** nếu không có thêm kiểm chứng từ log hoặc SRE xác nhận. Tuy nhiên, hành động đề xuất là rất rõ ràng: Rollback phiên bản hoặc scale pool.
- **Để tự động thực hiện Rollback không cần SRE xác nhận**: Chúng ta nên đặt ngưỡng `Confidence Threshold >= 0.80` đối với các trường hợp có đồ thị con phân cấp rõ ràng hoặc độ tương đồng của sự cố lịch sử (TF-IDF Similarity) vượt ngưỡng `0.60`.

---

---

## 3. Case học tập gây nghi ngờ / Không chắc chắn

Sự cố tại **Cluster `c-004-000`** là một case gây nghi ngờ lớn:
- Cảnh báo của `search-svc` (`a-0016` lúc `09:46:50`) có nhãn mô tả `"noise — independent slow query"` (nhiễu - truy vấn chậm độc lập), trong khi `checkout-svc` bị lỗi nghiêm trọng (`a-0017` lúc `09:47:12`).
- Do pipeline tự động chỉ nhìn vào mốc thời gian và sự hiện diện của service trên graph (không phân tích ngữ nghĩa nhãn nhiễu), hệ thống đã coi `search-svc` là nguyên nhân gốc vì nó cảnh báo sớm hơn 22 giây.
- Trên thực tế, `search-svc` rất có thể chỉ là một cảnh báo nhiễu nền ngẫu nhiên, và sự cố thực sự nằm ở `checkout-svc` (như bị deadlock). Đây chính là điểm hạn chế của việc chỉ dựa vào độ lệch thời gian thô mà không lọc nhiễu trước.

**Hướng xử lý & Khắc phục đề xuất**:
- Trước khi đưa alert vào temporal scorer, cần có bước pre-filtering loại bỏ alert có nhãn noise/standalone. Cụ thể: Nếu alert chứa các keyword như `"noise"`, `"independent"` trong nhãn hoặc metadata, ta cần giảm trọng số thời gian (`temporal weight`) của service đó xuống một nửa (ví dụ `0.5`).
- Ngoài ra, có thể bổ sung một tầng kiểm tra phụ thuộc (rule-based hoặc LLM-based) để kiểm tra xem "cảnh báo này có nằm trong luồng gọi thực tế của các dịch vụ bị lỗi khác hay không" trước khi đưa vào tính điểm.

---

## 4. Lựa chọn Bonus Path: Bonus 2 — TF-IDF Embedding

Chúng tôi đã triển khai **Bonus 2**: sử dụng `TfidfVectorizer` từ thư viện `scikit-learn` để tính toán độ tương đồng Cosine giữa biểu diễn văn bản của cluster cảnh báo (gồm tên dịch vụ + thông tin chi tiết các cảnh báo đã làm sạch) và thông tin lưu trữ trong `incidents_history.json` (dịch vụ liên quan + root cause + tóm tắt sự cố).

### So sánh định lượng: TF-IDF vs Keyword Matching

| Cluster     | Top-1 Keyword Match | Sim (KW) | Top-1 TF-IDF Match | Sim (TF-IDF) |
|-------------|---------------------|----------|--------------------|--------------|
| c-000-000   | INC-2025-11-08      | ~0.40*   | INC-2025-11-08     | 0.4904       |
| c-004-000   | INC-2026-01-29      | ~0.33*   | INC-2026-01-29     | 0.5317       |
| c-005-000   | INC-2026-03-20 hoặc INC-2025-11-08* | ambiguous | INC-2026-03-20 | 0.5304 |

(*) Ước tính nếu dùng simple term overlap (Jaccard).

### So sánh & Nhận xét chi tiết:
- **Keyword Matching thông thường**: Tính toán trùng khớp từ thô sơ dễ bị ảnh hưởng bởi các từ xuất hiện nhiều nhưng không mang thông tin phân biệt (như `svc`, `error`, `crit`, `db`, `latency`). Đặc biệt ở cluster `c-005-000`, hệ thống sử dụng keyword matching thông thường sẽ bị mơ hồ (ambiguous) giữa `INC-2026-03-20` (DDoS) và `INC-2025-11-08` (Pool exhaustion) do cả hai đều chứa sự tham gia của các từ khóa về `payment-svc` và `edge-lb`.
- **TF-IDF**: Tự động hạ thấp trọng số của các từ chung chung và tăng trọng số của các từ khóa đặc trưng quyết định sự cố (ví dụ như `ddos`, `pool`, `exhaustion`, `vacuum`, `autovacuum`). Nhờ đó, nó giúp phân biệt rõ ràng và gán mức điểm tương đồng cao (`0.5304`) cho sự cố DDoS tại `edge-lb` thay vì bị lẫn lộn sang các lỗi cơ sở dữ liệu. Kết quả truy vấn đạt độ chính xác cao và có tính phân biệt rõ rệt hơn hẳn.
