# DESIGN.md — MLOps Lifecycle: Anomaly Detection Pipeline

Tài liệu này trình bày các quyết định thiết kế cho hệ thống MLOps giám sát và tự động hóa chu kỳ (lifecycle) của mô hình phát hiện bất thường cho cổng thanh toán.

---

## 1. Ngưỡng Phát Hiện Drift (Drift Threshold)

* **Giá trị ngưỡng được chọn:** **0.15** (Tương đương 15% tổng số đặc trưng đầu vào bị drift theo Evidently DataDriftPreset).
* **Phân tích số liệu thực tế:**
  * Khi chạy thử nghiệm `drift_detector.py` trên chính dữ liệu cơ sở `baseline.csv` bằng cách chia tập theo tỷ lệ 70/30 (70% làm Reference, 30% làm Current), kết quả drift score thực đo được là **0.0** (0/3 đặc trưng bị drift). 
  * Ngưỡng **0.15** đại diện cho giá trị an toàn chống nhiễu. Khi chạy kiểm tra trên tập `drifted.csv`, drift score đạt **1.0000** (3/3 đặc trưng drift), vượt qua ngưỡng và kích hoạt tiến trình retrain.
  * **Lưu ý:** Con số `0.04` xuất hiện trong các tài liệu tham khảo trước đây thực chất là sự nhầm lẫn với khoảng cách Wasserstein thô trung bình của các đặc trưng (Wasserstein distance). Về mặt số học, vì hệ thống chỉ có 3 đặc trưng (`latency_p99`, `error_rate`, `rps`), tỷ lệ cột drift (`share_of_drifted_columns`) chỉ có thể nhận các giá trị rời rạc là `0.0`, `0.3333`, `0.6667`, hoặc `1.0`. Do đó, drift score thực tế ở điều kiện bình thường là **0.0**.
* **Đặc tính kiểm định thống kê của Evidently (Technical Note):**
  * Evidently tự động lựa chọn phương pháp kiểm định dựa trên kích thước mẫu (sample size): Sử dụng kiểm định Kolmogorov-Smirnov (KS-test) khi số dòng dữ liệu hiện tại $\le 1000$, và chuyển sang khoảng cách Wasserstein (Wasserstein distance) khi số dòng $> 1000$. 
  * Với `drifted.csv` (1008 dòng) hoặc baseline 70/30 split (1296 dòng), thuật toán sử dụng Wasserstein distance. Tuy nhiên, nếu kích thước tập kiểm tra thay đổi dưới 1000 dòng, phương pháp kiểm định sẽ tự động chuyển dịch, tạo ra rủi ro thay đổi hành vi drift score độc lập với phân phối dữ liệu thực tế.

---

## 2. Loại Drift & Số Liệu Thực Tế

* **Loại drift được giám sát:** **Data Drift** (lệch phân phối đầu vào) qua Evidently và **Performance Drift** (giám sát độ chính xác của mô hình trên dữ liệu có nhãn) để phát hiện Concept Drift.
* **Số liệu thực tế đo được trên `drifted.csv`:**
  * **Độ trễ (latency_p99):** Tăng từ 128.9ms (baseline) lên 162.4ms (drifted), tương ứng mức tăng thực tế là **+25.9%** (thông số thiết kế lý thuyết trong code là +30%).
  * **Tốc độ giao dịch (rps):** Tăng từ trung bình 450 lên 610, tương ứng mức tăng thực tế là **+30.3%** (thông số thiết kế lý thuyết trong code là +40% [`0.40 * drift_ramp`], còn câu chuyện nghiệp vụ mô tả là tăng 35%).
* **Giới hạn tham số huấn luyện (Contamination Mismatch):**
  * Mô hình IsolationForest được huấn luyện mặc định với tham số `contamination=0.03` (giả định 3% dữ liệu huấn luyện là bất thường). Tuy nhiên, nghiệp vụ hệ thống quy định dữ liệu baseline sạch hoàn toàn (0% bất thường).
  * Việc áp đặt giả định thống kê 3% này lên dữ liệu sạch khiến mô hình v1 luôn tự động gắn nhãn sai lệch (false positive) cho khoảng 3% dữ liệu bình thường, dẫn tới hiện tượng mô hình v1 cảnh báo sai chính xác **17/500 dòng (3.4%)** trên tập dữ liệu holdout sạch.
* **Ví dụ số cụ thể về tầm quan trọng của Combined Mode (Stress 1):**
  * Khi chạy mô hình v1 (huấn luyện trên baseline) trên tập dữ liệu `drifted.csv` (có nhãn):
    * Ở chế độ chỉ kiểm tra data drift (`--check-mode data`): Hệ thống ghi nhận dataset drift score = 1.0000 (3/3 đặc trưng drift) nhưng đây chỉ là tín hiệu nhị phân, không chỉ ra được mức độ suy giảm hiệu năng thực tế.
    * Ở chế độ kết hợp (`--check-mode combined` hoặc `performance`): Hệ thống phát hiện độ chính xác của mô hình v1 tụt giảm nghiêm trọng với **Precision ≈ 0.3164** và **Recall ≈ 0.7850**. Ví dụ số này chứng minh rằng gần 70% số cảnh báo của v1 trên dữ liệu mới là cảnh báo giả (false positives). Nếu không có combined mode, chúng ta sẽ không đo lường được mức độ thoái hóa trầm trọng của mô hình để lập kế hoạch ứng phó phù hợp.

---

## 3. Cấu hình Kích Hoạt Huấn Luyện Lại (Retrain Trigger Configuration)

* **Phương thức vận hành:** Bán tự động (Semi-automatic) thông qua **Manual Approval Gate** (Cổng phê duyệt thủ công).
* **Lý do lựa chọn phê duyệt thủ công:** ML Engineer cần kiểm tra hiệu năng thực tế của mô hình mới trước khi cutover để tránh đẩy một mô hình bị thoái hóa lên production làm ảnh hưởng trực tiếp đến SLA.
* **Giới hạn của trigger huấn luyện lại (Code vs Design Gap):**
  * Trong mã nguồn của Orchestrator (`retrain.py`), điều kiện tự động kích hoạt tiến trình retrain **chỉ dựa trên Data Drift** thông qua kết quả của Evidently (`drift_res.is_drift`). 
  * Nhánh phát hiện Performance/Concept Drift (`check_performance_drift`) không được tích hợp vào tiến trình trigger tự động của orchestrator mà chỉ tồn tại như một tùy chọn dòng lệnh (CLI) độc lập hoặc chạy trong quá trình giám sát sau triển khai (post-deploy). Đây là một giới hạn thiết kế của mã nguồn.
* **Cơ chế Approval Gate:** Hoàn toàn dựa trên con người (Human-in-the-loop). Code chỉ in thông tin Precision/Recall của mô hình v2 trên tập holdout ra màn hình và chờ nhập `y/N` để đổi alias. Code không có logic tự động chặn promotion nếu mô hình v2 có hiệu năng kém hơn v1 trên tập holdout.

---

## 4. Quản lý Phiên Bản & Khôi phục (Versioning & Rollback)

* **Chiến lược quản lý phiên bản:** Sử dụng **Registry Alias** của MLflow (`production`, `staging`, `archived`) để tách biệt code ứng dụng và việc quản trị mô hình.
* **Quy trình Rollback:** Khi phát hiện mô hình mới bị suy giảm hiệu năng, Orchestrator sẽ đổi tag alias của phiên bản lỗi sang `archived` và đưa alias phiên bản cũ về lại `production`, đồng thời gọi endpoint `/reload` trên Model Server để khôi phục.
* **Rủi ro mất đồng bộ cấu hình (Sync Issue):**
  * Hàm `reload_serve` chỉ in cảnh báo (`WARNING`) khi không kết nối được tới `serve.py` mà không ngăn cản tiến trình chạy tiếp. 
  * Nếu API Server đang bị sập (down) khi alias đã được đổi trên MLflow, Registry alias và mô hình đang chạy thực tế trên API Server sẽ bị mất đồng bộ (stale state) mà không có cơ chế tự động phát hiện hay rollback lại alias trên MLflow Registry.

---

## 5. Cải Tiến Thiết Kế: Sử Dụng scikit-learn Pipeline

* **Vấn đề của Code Mẫu:** Code mẫu gốc huấn luyện mô hình IsolationForest trên dữ liệu đã chuẩn hóa (`X_scaled`) nhưng khi serve API (`serve.py`) và chạy monitor (`retrain.py` ở holdout validation) lại đưa trực tiếp dữ liệu thô (`X`) vào dự đoán mà không chuẩn hóa.
* **Giải pháp Khắc Phục:** Chúng tôi thiết kế lại bằng cách đóng gói `StandardScaler` và `IsolationForest` vào một đối tượng **`Pipeline`** duy nhất của scikit-learn.
* **Hiệu quả:** Pipeline tự động chuẩn hóa dữ liệu đầu vào khi gọi `.predict(X)`, đảm bảo tính nhất quán toán học. 
* **Lưu ý quan trọng:** Lớp phục vụ API sản xuất thực tế (`serve.py`) sử dụng Pipeline chuẩn và **hoàn toàn không gặp lỗi scaler mismatch**. Lỗi này chỉ được giả lập một cách cố ý trong script giám sát của `retrain.py` để phục vụ stress test.

---

## 6. Chiến Lược Lựa Chọn Dữ Liệu Tái Huấn Luyện (Data Selection)

* **Chiến lược áp dụng:** **Sliding Window** (Cửa sổ trượt - kết hợp `baseline.csv` và `drifted.csv` thành 5328 dòng).
* **Phân tích so sánh thực nghiệm với chiến lược thay thế (Stress 2):**
  * **Chiến lược A (Chỉ huấn luyện trên cửa sổ drift - Drift-window-only):** Huấn luyện v2 trên 1008 dòng của tập `drifted.csv`. Kết quả là mô hình bị overfit nặng vào phân phối mới: tỷ lệ báo động giả (False Positive Rate - FPR) trên tập `holdout.csv` (pattern cũ) tăng vọt lên mức **8.60%**; trên tập `post_deploy_eval.csv`, Precision đạt **0.9524** và Recall đạt **1.0000**.
  * **Chiến lược B (Cửa sổ trượt kết hợp - Sliding Window baseline+drifted):** Huấn luyện v2 trên 5328 dòng kết hợp. Kết quả tối ưu vượt trội: tỷ lệ FPR trên tập `holdout.csv` duy trì ở mức **0.00%** (không có báo động giả); trên tập `post_deploy_eval.csv`, mô hình đạt hiệu năng tuyệt đối với Precision/Recall đều bằng **1.0000 / 1.0000**.
  * So sánh thực nghiệm này chứng minh rằng việc giữ lại dữ liệu lịch sử thông qua Sliding Window là bắt buộc để ngăn chặn mô hình quên các phân phối cũ vẫn có thể xảy ra trên thực tế.
* **Phân tích giới hạn của `holdout.csv` (Quan trọng):**
  * Trong tập dữ liệu mô phỏng `holdout.csv` (500 dòng), cột nhãn thực tế **`anomaly_label` bằng 0 đối với toàn bộ 500 dòng** (do dữ liệu baseline được sinh không vượt ngưỡng phát hiện bất thường nghiệp vụ).
  * Vì không có nhãn bất thường thực tế nào ($y_{\text{true}} = 0$), độ chính xác Precision ($tp / (tp+fp)$) của bất kỳ mô hình nào dự đoán trên tập này đều đạt **0.0000** về mặt toán học (do True Positive $tp = 0$).
  * Do đó, điều kiện kiểm tra hiệu năng trên holdout luôn luôn là $0.0 \ge 0.0$ (luôn đúng một cách hình thức), không mang ý nghĩa thực chất về mặt kiểm thử thống kê để so sánh giữa các chiến lược dữ liệu. Để giải quyết, chúng ta sử dụng chỉ số **FPR** (v1 FPR = 3.40% vs v2 FPR = 0.00%) làm thước đo so sánh thực chất.

---

## 7. Thiết Kế Tự Động Rollback (Auto-Rollback Policy)

* **Cơ chế kích hoạt:** Tự động rollback về v1 nếu Precision của mô hình mới giảm xuống dưới **0.65** trong vòng 24 chu kỳ giám sát đầu tiên trên tập dữ liệu `post_deploy_eval.csv`.
* **Phân tách giám sát thật và Rollback Simulation (Stress 3):**
  * Trong điều kiện giám sát thực tế trên môi trường production, mô hình Pipeline v2 chạy đầy đủ (có StandardScaler) đạt Precision tuyệt đối **1.0000** trên tập `post_deploy_eval.csv` (200 dòng, trong đó có 80 dòng bất thường thực sự). Do đó, trong thực tế hệ thống sẽ hoạt động ổn định và không bao giờ tự động rollback vô cớ.
  * Để phục vụ việc stress test và nghiệm thu tự động cơ chế rollback (Acceptance Criterion 6), chúng tôi thiết kế tham số `--simulate-degradation` trong `retrain.py`:
    * Khi bật cờ này: Hệ thống cố ý bỏ qua StandardScaler và truyền trực tiếp dữ liệu thô vào IsolationForest. Việc này mô phỏng lỗi suy thoái mô hình, kéo Precision tụt về đúng tỷ lệ bất thường nền là **80/200 = 0.4000** (< 0.65), kích hoạt thành công quy trình rollback và ghi nhận cả sự kiện `simulated_fault_injection` lẫn `auto_rollback_v2_to_v1` trong file audit log.
    * Khi tắt cờ (mặc định): Hệ thống chạy kiểm tra đầy đủ với Pipeline chính xác, đảm bảo tính năng giám sát hoạt động đúng đắn trên production.
