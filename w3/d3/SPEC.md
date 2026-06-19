# SPEC: AIOps Incident Detection and RCA Platform

## 1. Platform overview
The AIOps Incident Detection and RCA Platform is an automated system designed to monitor microservices, detect service degradation, correlate related alerts into cohesive incident groups, and perform Root Cause Analysis (RCA). The platform's scope includes collecting telemetry from Prometheus, running real-time anomaly detection, clustering alerts using topological relationships, and executing graph-based causal analysis. Non-scope includes direct automated mitigation (auto-healing), database migrations, and active traffic shaping.

## 2. SLO definition (from W3-D1)
- **Target SLO:**
  - **`api` service:** 99.9% availability over 30-day window (allowed downtime ~43 mins/month).
  - **`db` service:** 99.95% availability over 30-day window (allowed downtime ~22 mins/month).
  - **`frontend` service:** 99.0% availability over 30-day window (allowed downtime ~432 mins/month).
- **SLI:**
  - `api`: Latency < 500ms and HTTP status not in 5xx/429.
  - `db`: Database query latency < 100ms.
  - `frontend`: Page load DOM ready time < 3000ms with no JS or network errors.
- **Error budget:**
  - `api`: 20,738 allowed failed requests per month out of 20,737,800 total events.
  - `db`: 863 allowed failed queries per month out of 1,726,830 total queries.
  - `frontend`: 51,840 allowed failed requests per month out of 5,184,000 total loads.
- **Burn-rate alert tiers:**
  - **Tier 1 (Critical Page):** 14.4x burn rate (1h and 5m windows) -> page on-call immediately.
  - **Tier 2 (Warning Page):** 6x burn rate (6h and 30m windows) -> page on-call.
  - **Tier 3 (Ticket):** 1x burn rate (3d and 6h windows) -> create low-priority ticket.

## 3. Detection + Correlation + RCA stack (from W1+W2)
- **Detector:** Moving Window Median Absolute Deviation (MWMAD) thresholding over raw latencies and error rates from Prometheus. Output schema is a stream of labeled active alerts.
- **Correlator:** Spatio-temporal clusterer combining alerts that occur within a 120-second time window and share a path of maximum 2 hops in the topology graph. Output is an incident cluster with a primary alert.
- **RCA:** Topology-aware PageRank combined with metric Granger causality to calculate node influence on the service dependency graph. Output is a ranked list of candidate root causes with confidence scores.

## 4. Reliability validation (from W3-D2)
- **Chaos run cadence:** Weekly scheduled runs in the staging environment.
- **Detected/total ratio target:** 95% of injected faults successfully detected by the AIOps platform.
- **Steady-state signal:** Synthetic user-journey probes (external check) combined with core telemetry metric validation (Prometheus scrapers).

## 5. Operational pattern (from W3-D3)
- **Postmortem template:** [postmortem.md](file:///e:/WorkSpace/AIOPS/Week_01/w3/d3/postmortem.md) (Google SRE blameless format).
- **On-call rotation:** 24/7 primary/secondary SRE rotation model with escalations after 15 minutes of unacknowledged pages.
- **ADR repository:** [ADR.md](file:///e:/WorkSpace/AIOPS/Week_01/w3/d3/ADR.md) (Nygard format stored under `/w3/d3/`).

## 6. Cost model (from W3-D3)
- **Monthly cost:** $15,000 USD (Includes $1,000 compute/storage/licenses + 1.1 FTE equivalent at loaded SRE cost).
- **Break-even avoided incidents/month:** 1.5 avoided critical incidents per month (based on $12,000/hour downtime cost and 40% MTTR reduction).
- **See calculator:** [cost_model.py](file:///e:/WorkSpace/AIOPS/Week_01/w3/d3/cost_model.py)

## 7. Open risks
- **Risk 1 (Medium):** High dependency on dynamic topology graph accuracy; delayed changes in APM topology might cause incorrect RCA routing.
- **Risk 2 (Low):** Noise and alert fatigue if Granger causality window is too small during high-concurrency traffic spikes.
