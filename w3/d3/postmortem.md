# Postmortem: Cloudflare WAF Regex Outage (2019-07-02)

> Blameless wording — focusing on system properties and processes, not individuals.

## Summary
On 2019-07-02, a new Web Application Firewall (WAF) rule deployment introduced a regular expression that triggered catastrophic backtracking when evaluating incoming HTTP request queries. This caused CPU utilization to peg at 100% on all edge nodes globally, making the API service unresponsive. The incident lasted for approximately 27 minutes until the WAF rule was rolled back, resulting in a global drop in traffic of 82%.

## Impact
- **Users affected:** 82% of global users experiencing timeout/5xx errors
- **Services affected:** `api` service (WAF proxy middleware)
- **Revenue/SLA impact:** High SLA violation due to complete service unresponsiveness for 27 minutes
- **Duration:** 13:42 UTC → 14:09 UTC (27 minutes)

## Timeline (UTC)
Events extracted from the reproduced simulation environment (`timeline.json`):

| UTC | Event |
|-----|-------|
| 2019-07-02 13:42:00 | The new WAF regex rule deployment is promoted globally to all edge nodes. |
| 2019-07-02 13:42:05 | First user-visible symptom: API gateway response latencies exceed SLA thresholds. |
| 2019-07-02 13:42:10 | CPU utilization on all `api` containers spikes to 100% due to regex backtracking. |
| 2019-07-02 13:42:15 | AIOps pipeline detects anomalous latency jump and fires `HighCPUBacktracking` alert for `api`. |
| 2019-07-02 13:42:20 | Cascade of HTTP 5xx errors observed; AIOps pipeline fires `HTTP5xxRateHigh` alert. |
| 2019-07-02 13:45:00 | On-call engineer acknowledges the incident and begins investigation. |
| 2019-07-02 13:50:00 | Root cause is identified as the regex engine getting stuck in nested loops evaluating query strings. |
| 2019-07-02 14:08:00 | Mitigation action is initiated: command executed to roll back the offending rule and recreate the `api` containers. |
| 2019-07-02 14:09:00 | Containers successfully restarted; CPU utilization returns to baseline and API traffic recovers. |

## Root cause
The root cause is a regular expression with nested quantifiers `(?:(?:"|\d|.*)+(?:.*=.*))` deployed in a hot path in the WAF middleware, which triggers catastrophic backtracking (exponential evaluation path complexity) when matched against certain query input formats.

## Contributing factors
1. **Lack of Canary rollouts:** The WAF rule was deployed globally at once instead of in stages (e.g., 1% -> 10% -> 100%), leaving no canary buffer to isolate the blast radius.
2. **Missing engine guardrails:** The regex engine used in the middleware lacked evaluation timeout limits, allowing a single matching process to pin the CPU core indefinitely.
3. **Inadequate pre-deployment analysis:** Static analysis tools for checking ReDoS (Regular Expression Denial of Service) vulnerabilities were not integrated into the CI/CD pipeline.

## Detection
- **How was it detected?** Automatically detected by the AIOps pipeline based on CPU saturation and latency metrics, firing alerting states.
- **MTTD:** 10 seconds from deployment to alert trigger.
- **Pipeline gaps observed during reproduction:**
  - **Gap 1:** The pipeline did not correlate the deployment event with the CPU spike, showing a lack of configuration/change tracking integration.
  - **Gap 2:** Naive ranking flagged downstream 5xx errors as independent alerts rather than identifying them as cascading symptoms of the primary regex CPU bottleneck.

## Response
- **First responder action:** Isolated WAF rules and rolled back the configuration repository to the last stable commit.
- **Time to mitigate:** 26 minutes from first ack.
- **Time to fully resolve:** 27 minutes.

## Action items
| # | Action | Owner | Type | ETA |
|---|--------|-------|------|-----|
| 1 | Roll back the offending WAF rule and run validation | SRE Team | mitigation | Complete |
| 2 | Add evaluation timeouts (e.g. max 100ms) to the regex match engine | Core Dev | preventive | 2026-06-25 |
| 3 | Integrate static ReDoS scanners into pre-merge test suite | CI/CD Eng | preventive | 2026-06-30 |
| 4 | Transition WAF rules deployment to a canary rollout model (1% -> 10% -> 100%) | Release Eng | mitigation | 2026-07-05 |
