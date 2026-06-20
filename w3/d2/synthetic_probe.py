#!/usr/bin/env python3
"""synthetic_probe.py — Periodically polls checkout health endpoint and logs responses.
"""
import time
import requests
import sys

def main():
    if len(sys.argv) < 3:
        print("Usage: python synthetic_probe.py <url> <log_file>")
        sys.exit(1)
        
    url = sys.argv[1]
    log_file = sys.argv[2]
    
    print(f"[Probe] Starting synthetic probe for {url} writing to {log_file}")
    
    while True:
        ts = int(time.time())
        t0 = time.perf_counter()
        try:
            r = requests.get(url, timeout=10.0)
            latency = int((time.perf_counter() - t0) * 1000)
            if r.status_code == 200:
                log_line = f"{ts} pass {latency}\n"
            else:
                log_line = f"{ts} fail {r.status_code} {latency}\n"
        except requests.exceptions.RequestException:
            latency = int((time.perf_counter() - t0) * 1000)
            log_line = f"{ts} fail 504 {latency}\n"
            
        with open(log_file, "a") as f:
            f.write(log_line)
            f.flush()
            
        time.sleep(5.0)

if __name__ == "__main__":
    main()
