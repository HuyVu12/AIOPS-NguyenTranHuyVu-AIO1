# W3-D1 Design Documentation — Huy Vu

This document defends the SLI/SLO and alerting configurations based on baseline telemetry data and validation results.

---

### 1. SLI Choice for Frontend
For the frontend, we chose a composite SLI: **dom_ready_ms < 3000ms AND js_error == false AND network_error == false**. 
- **DOM Ready vs. Page Load Time**: Page load time can be artificially inflated by late-loading non-critical assets (e.g. large images or third-party tracking scripts). `dom_ready_ms` measures when the HTML document has been fully parsed and the DOM is interactive, representing a much more accurate user-perceived speed.
- **Why include JS and network errors**: A page that loads fast but suffers from Javascript errors is broken (e.g., buttons fail to trigger actions). Similarly, network errors block asset fetching. Therefore, availability must combine speed (latency) with functional correctness (no JS errors) and network reliability.

### 2. SLO Target for API
We chose a **99.9%** SLO target for API availability.
- **Baseline Data**: The API's baseline fail rate (HTTP 5xx + 429) is **0.3488%**, yielding a baseline availability of **99.65%** (`1 - 0.003488 = 0.9965`).
- **Target Selection**: Setting a 99.99% SLO would be unrealistic and set us up for immediate failure since our normal operation noise (0.35% fail rate) is 35x higher than the allowed budget (0.01%). Conversely, a 99% SLO is too lax, as it allows a 1% failure rate (nearly 3x our baseline noise), ignoring significant degradation. A 99.9% target is a realistic, premium standard that forces us to address major outages while accommodating daily fluctuations.

### 3. Latency Threshold p99
We set the API latency threshold at **500ms**.
- **Latency Distribution**: Based on the 7-day access logs (`access_log.jsonl` containing 2,073,780 requests):
  - `<100ms`: 1,950,118 requests (94.04%)
  - `100-200ms`: 114,706 requests (5.53%)
  - `200-300ms`: 5,439 requests (0.26%)
  - `300-500ms`: 2,283 requests (0.11%)
  - `500-1000ms`: 1,082 requests (0.05%)
  - `>=1000ms`: 152 requests (0.01%)
  - The baseline **p99 latency is 156ms**.
- **Defending the Choice**: By choosing 500ms, only **0.06%** of baseline requests (`(1082 + 152) / 2073780`) are marked as slow. This provides a generous safety buffer for random garbage collection pauses or transient network hiccups, ensuring they do not trigger false pages.

### 4. 4xx Exclusion
Standard HTTP 4xx client errors (excluding 429) are excluded from the error counts.
- **Rationale**: 4xx errors (e.g., 400 Bad Request, 401 Unauthorized, 404 Not Found) are triggered by client behavior, such as incorrect inputs, expired credentials, or scrapers scanning non-existent pages. They do not represent service unreliability.
- **Reference Data**: In the access logs, client errors are highly uniform and present on every single endpoint:
  - `/api/cart`: 2.04% 4xx (8,467 / 415,386)
  - `/api/checkout`: 2.01% 4xx (8,334 / 414,342)
  - `/api/orders`: 2.02% 4xx (8,360 / 414,811)
  - `/api/products`: 2.02% 4xx (8,352 / 414,335)
  - `/api/user`: 1.98% 4xx (8,199 / 414,906)
  - If we included 4xx, our normal error rate would be around **2.35%**, instantly violating the 0.1% budget for our 99.9% SLO. HTTP 429 is kept because rate-limiting represents load shedding, indicating system resource constraints.

### 5. MWMBR Tuning
We utilized the default Google SRE Workbook parameters:
- **Tier 1 (Critical)**: 14.4x burn rate (1h & 5m windows) -> page
- **Tier 2 (Warning)**: 6x burn rate (6h & 30m windows) -> page
- **Tier 3 (Ticket)**: 1x burn rate (3d & 6h windows) -> ticket
- **Validation Results**: Replaying the logs against these default settings yielded excellent metrics:
  - **Fired Alerts**: 3 (down from 22 in static baseline, achieving a **86.4% noise reduction**).
  - **True Positives (TP)**: 3, **False Positives (FP)**: 0, **False Negatives (FN)**: 0.
  - **MTTD Delta**: 60s (indicating a 1-minute detection latency, which is optimal for minute-by-minute aggregation).
  - Since the default values achieved maximum safety (0 False Negatives) and exceptional noise reduction (0 False Positives), no further tuning was required.
