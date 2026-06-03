import csv
import json
import os
import time
from collections import deque
from queue import Empty, Queue
from threading import Thread
import math

DATA_PATH = r"datasets\NAB\data\realKnownCause\machine_temperature_system_failure.csv"
OUTPUT_PATH = "features.json"
WINDOW_SIZE = 12  # 12 rows * 5 mins = 60 mins (1 tiếng window)

def producer(data_path: str, event_queue: Queue):
    """
    Mock Kafka Producer: Đọc dữ liệu từ file CSV và emit từng dòng vào Queue.
    """
    print("[Producer] Starting...")
    if not os.path.exists(data_path):
        print(f"[Producer] Error: File {data_path} không tồn tại!")
        event_queue.put(None) 
        return

    with open(data_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            event_queue.put(row)
            time.sleep(0.001) 
            
    event_queue.put(None)
    print("[Producer] Finished emitting all events.")


def consumer(event_queue: Queue, output_path: str, window_size: int):
    """
    Mock Flink/Spark Streaming Consumer: Nhận stream, tính rolling features bằng Window Buffer.
    """
    print("[Consumer] Starting...")
    window_buffer = deque(maxlen=window_size)
    processed_events = []
    
    prev_value = None

    while True:
        try:
            event = event_queue.get(timeout=5)
            
            # Kiểm tra tín hiệu Poison Pill kết thúc stream
            if event is None:
                print("[Consumer] Poison pill received. Stopping...")
                break
            
            # Extract dữ liệu gốc
            timestamp = event["timestamp"]
            current_value = float(event["value"])
            
            # Thêm vào sliding window buffer
            window_buffer.append(current_value)
            
            # 1. Tính toán Rolling Mean
            rolling_mean = sum(window_buffer) / len(window_buffer)
            
            # 2. Tính toán Rolling Std (Độ lệch chuẩn)
            if len(window_buffer) > 1:
                variance = sum((x - rolling_mean) ** 2 for x in window_buffer) / (len(window_buffer) - 1)
                rolling_std = math.sqrt(variance)
            else:
                rolling_std = 0.0
                
            # 3. Tính toán Rate of Change (Tốc độ thay đổi so với dòng ngay trước)
            if prev_value is not None:
                rate_of_change = current_value - prev_value
            else:
                rate_of_change = 0.0
                
            # Cập nhật lại giá trị cũ cho vòng lặp sau
            prev_value = current_value

            # Tạo feature record mới
            feature_record = {
                "timestamp": timestamp,
                "original_value": current_value,
                "rolling_mean": round(rolling_mean, 4),
                "rolling_std": round(rolling_std, 4),
                "rate_of_change": round(rate_of_change, 4)
            }
            
            processed_events.append(feature_record)
            event_queue.task_done()
            
        except Empty:
            print("[Consumer] Queue empty timeout. Stopping...")
            break

    # Ghi toàn bộ kết quả thu được ra file JSON thông qua định dạng JSONL (hoặc JSON array)
    print(f"[Consumer] Writing features to {output_path}...")
    with open(output_path, mode="w", encoding="utf-8") as f:
        json.dump(processed_events, f, indent=4)
    print("[Consumer] Features successfully saved.")


if __name__ == "__main__":
    # Khởi tạo Python Queue đóng vai trò Message Broker (Kafka)
    shared_queue = Queue(maxsize=1000)
    
    producer_thread = Thread(target=producer, args=(DATA_PATH, shared_queue))
    consumer_thread = Thread(target=consumer, args=(shared_queue, OUTPUT_PATH, WINDOW_SIZE))
    
    start_time = time.time()
    
    producer_thread.start()
    consumer_thread.start()
    
    producer_thread.join()
    consumer_thread.join()
    
    print(f"\n[Pipeline] Completed in {time.time() - start_time:.2f} seconds.")