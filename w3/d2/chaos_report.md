# Chaos Engineering Report — Nguyen Tran Huy Vu

## 1. Setup
- **Stack version:** `v2.4.1` (commit hash: `f2a7db3`)
- **Pipeline version:** `v1.2.0` (commit hash: `c89ab2f`)
- **Baseline window:** 2026-06-18 03:00:00 UTC → 2026-06-18 03:05:00 UTC
- **Total experiments run:** 10

## 2. Results table
```
==== Chaos Run ====
Total: 10
Detected: 8/10
RCA correct: 7/8
False alarms in baseline windows: 0
Precision: 1.00
Recall: 0.80
MTTD p50: 22s, p95: 45s

Per-experiment:
| # | name              | detected | mttd  | rca_service  | rca_correct |
|---|-------------------|----------|-------|--------------|-------------|
| 1 | payment_latency   | Y        | 29s   | payment-svc  | Y           |
| 2 | payment_loss      | Y        | 22s   | payment-svc  | Y           |
| 3 | inventory_kill    | Y        | 13s   | inventory-svc | Y           |
| 4 | gateway_cpu       | Y        | 16s   | api-gateway  | Y           |
| 5 | db_memory         | Y        | 28s   | payment-svc  | N           |
| 6 | auth_skew         | Y        | 45s   | auth-svc     | Y           |
| 7 | log_disk          | N        | —     | —            | N           |
| 8 | gateway_partition | Y        | 19s   | frontend     | Y           |
| 9 | dns_latency       | N        | —     | —            | N           |
| 10 | checkout_retry_storm | Y        | 10s   | inventory-svc | Y           |
```

## 3. Detailed per-experiment analysis

### Experiment 1: payment_latency
- **Hypothesis:** Delaying `payment-svc` network egress by 500ms will trip latency thresholds on `api-gateway`, triggering a pipeline alert within 30s. RCA should correctly trace it to `payment-svc`.
- **Observed:** Detected with an MTTD of 29 seconds. The RCA engine matched the alert cascade to the dependency graph and correctly identified `payment-svc` as the root cause service, finding a matching historical incident (INC-2026-01-04) with similarity 0.4837. This matched the expected hypothesis.

### Experiment 2: payment_loss
- **Hypothesis:** Injecting 30% packet loss on `payment-svc` egress will cause TCP retries, leading to slow transactions and a high HTTP error rate. Pipeline should detect it and point to `payment-svc`.
- **Observed:** Detected with an MTTD of 22 seconds. The error rate was caught, and the RCA engine correctly identified `payment-svc` as the root cause service by matching historical incident INC-2026-01-04 (similarity 0.5435). This matched the expected hypothesis.

### Experiment 3: inventory_kill
- **Hypothesis:** Killing `inventory-svc` containers periodically every 60s will cause connection dropouts. Pipeline should trigger availability alerts and point to `inventory-svc`.
- **Observed:** The availability alert triggered at 13 seconds. The RCA engine correctly identified `inventory-svc` as the root cause service, matching historical incident INC-2025-10-15 (similarity 0.6642). This matched the expected hypothesis.

### Experiment 4: gateway_cpu
- **Hypothesis:** Stressing the `api-gateway` CPU to 90% will cause high latency propagation across all microservices. The correlator should group the alerts and RCA should target the gateway itself.
- **Observed:** The pipeline registered the CPU alert at 16 seconds. The RCA engine correctly targeted the gateway itself (`api-gateway`) through fallback graph PageRank because it was the earliest node in the path, matching our hypothesis.

### Experiment 5: db_memory
- **Hypothesis:** Filling `payment-db` memory to 95% will block write queries. This will exhaust the connection pool on the calling application `payment-svc`. RCA should identify the database as the root cause.
- **Observed:** Detected with an MTTD of 28 seconds. The detector successfully fired both the application caller alert (`payment_latency_alert` at 28s) and the target database alert (`db_memory_alert` at 32s). However, the RCA engine wrongly picked the application tier caller `payment-svc` instead of the database `payment-db` as the root cause (rca_correct: N). This represents a misdiagnosis in the RCA retrieval layer, where the TF-IDF query incorrectly matched the cluster to historical incident `INC-2026-01-04` (a `payment-svc` latency issue) with similarity 0.4837, rather than correctly identifying the database.

### Experiment 6: auth_skew
- **Hypothesis:** Skewing the clock of `auth-svc` by +60s will invalidate JWT/security tokens, causing auth handshake checkouts to fail. Pipeline should alert on auth failure rate and trace to `auth-svc`.
- **Observed:** The token signature discrepancies generated HTTP 401 unauthenticated requests. The pipeline raised an alert at 45 seconds. The correlation mapped the anomaly node correctly to `auth-svc`. This matched the expected hypothesis.

### Experiment 7: log_disk
- **Hypothesis:** Filling the `log-collector` disk to 95% will prevent log writing. The pipeline should capture log ingestion lags and alert on `log-collector`.
- **Observed:** **Not Detected.** The pipeline remained silent during the entire 120-second injection period. This represents a significant detection gap. The lack of standard disk-metrics scrapers in the monitoring pipeline allowed the disk exhaustion to pass unnoticed, failing our hypothesis.

### Experiment 8: gateway_partition
- **Hypothesis:** A full network partition between `frontend` and `api-gateway` for 30s will trigger severe timeout errors. The pipeline should identify the network border anomaly and trace to `frontend`.
- **Observed:** The network partition caused timeout failures. The pipeline detected it at 19 seconds. The RCA engine successfully identified `frontend` as the point of entry failure, matching our hypothesis.

### Experiment 9: dns_latency
- **Hypothesis:** A 2-second DNS query delay on `dns-resolver` will cause intermittent name resolution timeouts for downstream microservices. Pipeline should identify it and point to the DNS service.
- **Observed:** **Not Detected.** The name resolution delays caused sporadic timeouts, but because they occurred intermittently and did not cross the default 5-minute sliding window average anomaly thresholds, the pipeline did not raise any alerts. This failed our hypothesis.

### Experiment 10: checkout_retry_storm
- **Hypothesis:** Injecting 20% HTTP 500 errors on `checkout-svc` will trigger client retries, flooding `payment-svc` and `inventory-svc`. RCA must NOT pick `checkout-svc` as the root cause but identify the upstream dependencies instead.
- **Observed:** Detected with an MTTD of 10 seconds. The RCA engine successfully ignored the symptom-carrier `checkout-svc` and identified `inventory-svc` (one of the overloaded upstream dependencies) as the root cause, which matches the ground truth requirement (`NOT checkout-svc`).

---

## 4. Gap analysis — top 3 pipeline weaknesses

### Gap 1: Disk Space Exhaustion Silent Failures (Experiment 7)
- **Symptom:** Ingestion lag and disk capacity spikes went undetected by the AIOps platform.
- **Cause:** The monitoring daemon does not scrape disk space usage metrics (`node_filesystem_free_bytes`) or log shipper queue lags from the `log-collector` service.
- **Recommended fix:** Integrate `node-exporter` filesystem collector on all storage nodes and add a static burn-rate alert for disk usage exceeding 90%.

### Gap 2: Inability to Detect Intermittent Latency Fluctuations (Experiment 9)
- **Symptom:** DNS query delays of 2s failed to trigger any alarms in the AIOps platform.
- **Cause:** Anomaly thresholds are calculated using 5-minute moving average windows (MWMAD), which smooths out short, intermittent latency anomalies.
- **Recommended fix:** Transition to percentile-based latency tracking (p99 or p95 thresholds) over smaller evaluation windows (1 minute) to detect name resolution jitter.

### Gap 3: Clock Skew Detection Latency (Experiment 6)
- **Symptom:** The time skew anomaly on the authentication service took 45 seconds to detect, which is too slow for token validation failures.
- **Cause:** Lack of time sync checking (NTP tracking) metrics. The pipeline had to wait for user authentication failures to reach a statistical anomaly threshold.
- **Recommended fix:** Scrap node clock offsets from NTP/chrony. Raise proactive time skew alerts immediately if the clock drift exceeds 1 second.
