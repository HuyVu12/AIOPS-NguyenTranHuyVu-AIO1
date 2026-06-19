# ADR-007: Implement Topology-Aware Causal RCA over Count-Based Ranking

## Status
proposed | **accepted**

## Context
During the reproduction of the Cloudflare 2019 WAF regex outage, we observed a major gap in our detection pipeline (Gap 2 in postmortem): the AIOps platform triggered separate independent alarms for the CPU bottleneck (`HighCPUBacktracking`) and the downstream HTTP 5xx errors (`HTTP5xxRateHigh`). A naive alert ranker would rank the 5xx errors higher due to alert volume, failing to identify the upstream `api` WAF regex engine as the single root cause. We need a routing and root cause analysis (RCA) algorithm that accounts for system topology and event timings (causal lag) to solve this cascading alert correlation issue.

## Decision
We will implement a topology-aware causal Root Cause Analysis algorithm inside our AIOps platform. The RCA pipeline will build a directed service dependency graph and calculate node influence using a combination of PageRank (topology distance from edge) and Granger causality (causal lag of metrics drift) to rank alerts.

## Alternatives considered

1. **Count-Only Ranking**
   * *Why rejected:* Simple and fast to implement, but heavily susceptible to alert storms. In cascading failures, downstream services retry and generate significantly more alerts than the upstream root cause, leading to false rankings.
2. **LLM-Only RCA**
   * *Why rejected:* Highly flexible and able to parse unstructured log data, but introduces hallucinations, is non-deterministic, and suffers from high latency and API cost. It cannot be used as the primary real-time ranking engine.
3. **Graph-Only PageRank (without temporal data)**
   * *Why rejected:* While it accurately reflects the static system dependency, it fails to capture temporal causality. It cannot distinguish between two services on the same path when only one is active.

## Consequences
- **Positive:** Effectively identifies the exact root cause of cascading failures (such as the regex CPU bottleneck causing downstream HTTP 5xx errors) and reduces alert fatigue.
- **Positive:** Highly composable; the algorithm degrades gracefully to graph-only ranking if temporal metric data is missing.
- **Negative (Trade-off):** Higher compute overhead due to dynamic Granger causality computations over lag windows.
- **Negative (Trade-off):** Requires maintaining an up-to-date service dependency graph, adding operational overhead to integrate with service meshes or APM tools.
