# log_analyzer.py
import sys
import os
import re
from datetime import datetime, timedelta
import pandas as pd
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
def extract_timestamp_and_content(log_line):
    """
    Hàm sử dụng Regex tự động nhận diện Datetime của nhiều loại log thô:
    1. Hadoop/Spark standard (YYYY-MM-DD HH:MM:SS)
    2. BGL Supercomputer (YYYY-MM-DD-HH.MM.SS.xxxxxx)
    3. HDFS Raw (YYMMDD HHMMSS)
    """
    line = log_line.strip()
    if not line:
        return None, None

    # KỊCH BẢN 1: Dạng Hadoop/Spark Log (Bắt đầu bằng YYYY-MM-DD HH:MM:SS)
    match_hadoop = re.match(r'^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})', line)
    if match_hadoop:
        dt_str = f"{match_hadoop.group(1)} {match_hadoop.group(2)}"
        try:
            dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            content = line[match_hadoop.end():].strip()
            return dt_obj, content
        except:
            pass

    # KỊCH BẢN 2: Dạng BGL Log (Thời gian dạng YYYY-MM-DD-HH.MM.SS nằm ở giữa)
    match_bgl = re.search(r'(\d{4}-\d{2}-\d{2})-(\d{2})\.(\d{2})\.(\d{2})', line)
    if match_bgl:
        dt_str = f"{match_bgl.group(1)} {match_bgl.group(2)}:{match_bgl.group(3)}:{match_bgl.group(4)}"
        try:
            dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            content = line[match_bgl.end():].strip()
            return dt_obj, content
        except:
            pass

    # KỊCH BẢN 3 (MỚI BỔ SUNG): Dạng HDFS Raw Log (Bắt đầu bằng YYMMDD HHMMSS)
    # Ví dụ: 081109 203615 148 INFO...
    match_hdfs_raw = re.match(r'^(\d{6})\s+(\d{6})', line)
    if match_hdfs_raw:
        dt_str = f"{match_hdfs_raw.group(1)} {match_hdfs_raw.group(2)}"
        try:
            dt_obj = datetime.strptime(dt_str, "%y%m%d %H%M%S") # %y viết thường cho năm 2 chữ số
            content = line[match_hdfs_raw.end():].strip()
            return dt_obj, content
        except:
            pass

    return None, None
def analyze_raw_log(file_path):
    if not os.path.exists(file_path):
        print(f"❌ Lỗi: Không tìm thấy file tại đường dẫn: {file_path}")
        sys.exit(1)

    # Đọc toàn bộ file log thô dưới dạng text từng dòng
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        raw_lines = f.readlines()

    total_lines = len(raw_lines)
    if total_lines == 0:
        print("⚠️ File log trống.")
        return

    # Cấu hình bộ Parser Drain3
    config = TemplateMinerConfig()
    config.drain_sim_th = 0.5
    config.drain_depth = 4
    miner = TemplateMiner(config=config)

    parsed_data = []

    # Tiến hành duyệt và trích xuất thông minh
    for line in raw_lines:
        dt_obj, content = extract_timestamp_and_content(line)
        
        # Nếu dòng log không chứa thời gian nhận diện được, map tạm vào mốc của dòng trước đó
        if dt_obj is None:
            if len(parsed_data) > 0:
                dt_obj = parsed_data[-1]['timestamp']
                content = line.strip()
            else:
                continue # Bỏ qua nếu dòng đầu file bị lỗi cấu trúc
                
        result = miner.add_log_message(content)
        parsed_data.append({
            'timestamp': dt_obj,
            'template_id': f"T_{result['cluster_id']:03d}",
            'template_text': result['template_mined']
        })

    if len(parsed_data) == 0:
        print("❌ Lỗi: Không nhận diện được cấu trúc Datetime từ file log thô này.")
        sys.exit(1)

    df_parsed = pd.DataFrame(parsed_data)
    unique_templates_count = len(miner.drain.clusters)

    # Thống kê Top-5
    df_stats = df_parsed.groupby('template_id').size().reset_index(name='count')
    df_stats['percentage'] = (df_stats['count'] / len(df_parsed)) * 100
    df_stats = df_stats.sort_values(by='count', ascending=False).reset_index(drop=True)
    
    template_map = {f"T_{c.cluster_id:03d}": c.get_template() for c in miner.drain.clusters}

    # Xử lý Time-Window phát hiện Spike & New Templates trong 1 giờ gần nhất
    max_time = df_parsed['timestamp'].max()
    one_hour_threshold = max_time - timedelta(hours=1)

    df_history = df_parsed[df_parsed['timestamp'] < one_hour_threshold]
    df_recent = df_parsed[df_parsed['timestamp'] >= one_hour_threshold]

    # Tính toán Baseline lịch sử
    if not df_history.empty:
        total_hist_hours = (df_history['timestamp'].max() - df_history['timestamp'].min()).total_seconds() / 3600.0
        if total_hist_hours <= 0: total_hist_hours = 1.0
        hist_counts = df_history.groupby('template_id').size() / total_hist_hours
    else:
        hist_counts = pd.Series(dtype=float)

    recent_counts = df_recent.groupby('template_id').size()

    # Spike Detection
    spike_templates = []
    for tid, r_count in recent_counts.items():
        h_avg = hist_counts.get(tid, 0)
        if h_avg > 0 and r_count > (h_avg * 2):
            spike_templates.append({'template_id': tid, 'recent': r_count, 'hist_avg': round(h_avg, 2)})

    # New Templates Detection
    history_unique_ids = set(df_history['template_id'].unique()) if not df_history.empty else set()
    recent_unique_ids = set(df_recent['template_id'].unique())
    new_template_ids = recent_unique_ids - history_unique_ids

    # =====================================================================
    # XUẤT KẾT QUẢ RA STDOUT
    # =====================================================================
    print("=" * 75)
    print("                 🔥 RAW LOG ANALYZER STANDALONE REPORT 🔥                 ")
    print("=" * 75)
    print(f"📊 Tổng số dòng log xử lý   : {total_lines} dòng")
    print(f"📊 Số lượng Templates Unique : {unique_templates_count} nhóm")
    print("-" * 75)
    
    print("🔝 TOP-5 TEMPLATES XUẤT HIỆN NHIỀU NHẤT:")
    for idx, row in df_stats.head(5).iterrows():
        print(f" [{row['template_id']}] Count: {row['count']:4} ({row['percentage']:5.2f}%) | Cấu trúc: {template_map[row['template_id']][:65]}...")

    print("-" * 75)
    print(f"🚨 TEMPLATE TĂNG ĐỘT BIẾN TRONG 1 GIỜ GẦN NHẤT (Mốc: {one_hour_threshold.strftime('%Y-%m-%d %H:%M:%S')}) :")
    if spike_templates:
        for spike in spike_templates:
            print(f" 🔴 [{spike['template_id']}] Số lượng gần đây: {spike['recent']} (Trung bình lịch sử/giờ: {spike['hist_avg']})")
    else:
        print(" ✅ Không phát hiện hiện tượng giật đỉnh log (Spike).")

    print("-" * 75)
    print("🆕 TEMPLATE MỚI XUẤT HIỆN TRONG 1 GIỜ GẦN NHẤT:")
    if new_template_ids:
        for n_id in new_template_ids:
            print(f" ✨ [{n_id}] Cấu trúc mới: {template_map[n_id][:75]}...")
    else:
        print(" ✅ Không ghi nhận cấu trúc template mới xuất hiện.")
    print("=" * 75)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("👉 Hướng dẫn chạy: python log_analyzer.py <path_to_raw_log_file>")
        sys.exit(1)
        
    analyze_log_file = sys.argv[1]
    analyze_raw_log(analyze_log_file)