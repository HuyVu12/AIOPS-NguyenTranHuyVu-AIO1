# SUBMIT.md — Reflection: MLOps Lifecycle Lab

## Câu 1: Ngưỡng phát hiện drift (Drift threshold) bạn chọn là bao nhiêu và tại sao? Có kiểm chứng trên dữ liệu thực tế không?

* **Ngưỡng chọn:** **0.15** (15% các đặc trưng bị lệch phân phối).
* **Kiểm chứng thực tế:** Có, khi chia tập dữ liệu sạch `baseline.csv` theo tỷ lệ 70/30 để kiểm tra ở trạng thái bình thường (không drift), kết quả drift score thực tế đo được là **0.0** (0/3 đặc trưng bị drift). 
* **Lý giải về mặt số học:** Vì hệ thống chỉ giám sát 3 đặc trưng đầu vào (`latency_p99`, `error_rate`, `rps`), tỷ lệ các cột bị drift (`share_of_drifted_columns`) chỉ có thể nhận các giá trị rời rạc là `0.0`, `0.3333`, `0.6667`, hoặc `1.0`. Con số `0.04` xuất hiện trong tài liệu tham khảo trước đây thực chất là khoảng cách Wasserstein thô trung bình của các đặc trưng, chứ không phải drift score dạng tỷ lệ cột. 
* Do đó, ngưỡng **0.15** (lớn hơn nhiễu nền 0.0) là tối ưu để loại bỏ các cảnh báo giả do biến động chu kỳ tự nhiên. Khi chạy thực tế trên tập `drifted.csv`, drift score đạt **1.0000** (3/3 đặc trưng drift), vượt qua ngưỡng và kích hoạt hệ thống huấn luyện lại một cách chính xác.

---

## Câu 2: Điều gì xảy ra nếu mô hình v2 sau khi huấn luyện lại hoạt động kém hơn v1 trong production? Quy trình của bạn xử lý trường hợp này thế nào?

* **Giới hạn và cải tiến của bước holdout validation (Critique & Implementation):**
  * Trong tập dữ liệu `holdout.csv` (500 dòng), nhãn `anomaly_label` đều bằng 0, khiến Precision toán học của mọi mô hình đều bằng 0.0000. Để đánh giá thực chất, hệ thống đã được nâng cấp để so sánh tỷ lệ báo động giả (False Positive Rate - FPR) của v1 và v2 trên holdout. 
  * Kết quả thực nghiệm: Mô hình v1 có FPR = **3.40%** (do contamination=0.03 trên baseline sạch), trong khi mô hình v2 (huấn luyện cửa sổ trượt Sliding Window baseline+drifted) cải thiện FPR xuống mức **0.00%** (không báo động giả), đảm bảo v2 hoạt động tốt hơn v1 trước khi promote.
* **Xử lý tự động sau khi deploy (Auto-Rollback & Phân tách degradation test):**
  * Sau khi v2 được promote lên production, `post_deploy_monitor` sẽ giám sát hiệu năng trên tập `post_deploy_eval.csv` (200 dòng, 40% là bất thường thực sự). Ở chế độ chạy thực tế, mô hình Pipeline chuẩn (có scaler) đạt Precision tuyệt đối **1.0000**, hệ thống chạy ổn định.
  * Để kiểm thử tự động cơ chế rollback (Stress 3), ta sử dụng cờ `--simulate-degradation`. Khi bật cờ này, hệ thống sẽ cố ý bỏ qua StandardScaler, kéo Precision tụt về đúng tỷ lệ bất thường nền là **80/200 = 0.4000**.
  * Vì **0.4000 < 0.65** (ngưỡng rollback), hệ thống sẽ lập tức kích hoạt auto-rollback: hạ cấp v2 xuống `archived`, khôi phục v1 về lại `production` và reload Model Server, ghi nhận cả sự kiện `simulated_fault_injection` lẫn `auto_rollback_v2_to_v1` trong file audit log.

---

## Câu 3: Sự khác biệt giữa data drift và concept drift là gì? Thư viện Evidently phát hiện loại nào trong bài Lab này?

* **Data drift (Lệch dữ liệu):** Xảy ra khi phân phối của dữ liệu đầu vào thay đổi ($P(X)$ thay đổi) nhưng mối quan hệ chuyển đổi từ đầu vào sang nhãn đầu ra $P(Y|X)$ giữ nguyên. Ví dụ: latency baseline trung bình tăng từ 128.9ms lên 162.4ms (tăng thực tế **+25.9%** so với baseline) sau khi tích hợp bên thứ ba.
* **Concept drift (Lệch khái niệm):** Xảy ra khi mối quan hệ giữa các đặc trưng đầu vào và nhãn đầu ra thay đổi ($P(Y|X)$ thay đổi). 
* **Evidently:** Trong bài Lab này, Evidently sử dụng gói `DataDriftPreset` thực hiện kiểm định giả thuyết thống kê trên phân phối dữ liệu đầu vào để phát hiện **Data drift**.
* **Lưu ý về tập dữ liệu mô phỏng (Critique):** Concept drift trong tập `drifted.csv` được mô phỏng bằng cách đảo ngẫu nhiên nhãn của 25% số dòng dữ liệu (`CONCEPT_DRIFT_FLIP_FRACTION = 0.25`). Đây thực chất là hành vi tiêm nhiễu nhãn ngẫu nhiên (random label noise injection) chứ chưa phải một sự thay đổi luật nghiệp vụ có cấu trúc thực tế (ví dụ: thay đổi ngưỡng lỗi hay độ trễ theo thời gian). Hệ thống phát hiện Concept drift gián tiếp thông qua việc kiểm tra độ sụt giảm hiệu năng Precision của mô hình cũ trên dữ liệu mới có nhãn.

---

## Câu 4: Tại sao việc thực hiện Blue-Green Swap (đảo phiên bản qua alias) lại quan trọng hơn việc ghi đè trực tiếp file mô hình?

* **Tránh Race Condition (Xung đột ghi đọc):** Khi ghi đè trực tiếp file mô hình trên ổ đĩa, nếu Server đang xử lý yêu cầu dự đoán từ khách hàng tại thời điểm ghi đè, nó có thể đọc phải tệp mô hình bị hỏng (corrupted read) dẫn tới lỗi hệ thống hoặc dự đoán sai lệch hoàn toàn.
* **Không Downtime:** Với Blue-Green Swap, cả hai mô hình v1 và v2 đều nằm song song trên MLflow Server. Khi đổi phiên bản, ta chỉ chuyển đổi alias `production` từ v1 sang v2 trên Registry và gọi endpoint `/reload`. API Server sẽ chuyển sang mô hình mới một cách mượt mà và không cần khởi động lại dịch vụ.
* **Khả năng Rollback lập tức:** Nếu mô hình mới có lỗi phát sinh, ta có thể đảo ngược alias về v1 và reload lại mô hình cũ chỉ trong chưa đầy 1 giây. Nếu ghi đè file trực tiếp, file cũ sẽ bị mất vĩnh viễn và không thể rollback nhanh chóng.

---

## Câu 5: Nếu bạn phải tự động hóa cổng phê duyệt (không cần con người can thiệp), bạn sẽ sử dụng metric và ngưỡng nào để tự động nâng cấp mô hình?

Nếu tự động hóa hoàn toàn cổng phê duyệt, tôi sẽ sử dụng các điều kiện kiểm thử đồng thời trên tập dữ liệu kiểm tra validation:

1. **Độ lệch tỷ lệ bất thường (Anomaly Rate Delta):**
   $$\left| \text{anomaly\_rate}_{\text{v2}} - \text{anomaly\_rate}_{\text{v1}} \right| < 0.05$$
   Đảm bảo hành vi của mô hình mới không thay đổi quá đột ngột so với mô hình cũ (tránh báo động giả hàng loạt).
2. **Ngưỡng giới hạn tỷ lệ bất thường tuyệt đối:**
   $$0.01 < \text{anomaly\_rate}_{\text{v2}} < 0.10$$
   Tránh trường hợp mô hình bị suy thoái nghiêm trọng dẫn đến việc phân loại toàn bộ giao dịch là bất thường (tỷ lệ > 10%) hoặc không phát hiện được bất kỳ bất thường nào (tỷ lệ < 1%).
3. **Độ chính xác trên tập dữ liệu Holdout thực tế:**
   $$\text{Precision}_{\text{v2}} \geq \text{Precision}_{\text{v1}}$$
   Đảm bảo hiệu năng của mô hình mới trên các mẫu dữ liệu lịch sử không bị suy giảm (với điều kiện tập dữ liệu holdout được thiết kế chuẩn có chứa cả hai nhãn 0 và 1).
