# FINDINGS.md — Reflection Questions

This document answers the five reflection questions from HANDOUT §5 based on the execution of our remediation engine.

---

### 1. Which similarity function did you choose for Layer 2, and why?
We chose a structured, multi-signal similarity function combining log, trace, and service similarities:
$$Similarity = 0.5 \times S_{log} + 0.3 \times S_{trace} + 0.2 \times S_{svc}$$
Where:
* $S_{log}$ is a keyword-based Jaccard similarity of active log signatures.
* $S_{svc}$ is the Jaccard index between the affected services sets.
* $S_{trace}$ is the Jaccard overlap of anomalous trace edges (P99 deviation ratio $\ge 1.5$ or error rate $\ge 0.05$) multiplied by $(1 - MAE)$ of common anomalous edges.

**Alternative Considered:**
We considered using a flat cosine similarity on embeddings or a simple Euclidean distance on all metrics/traces.

**Empirical Reason for Selection:**
Flat distances suffer from high dimensionality noise and fail to capture structural shifts in network traffic. Jaccard similarity on anomalous trace edges ensures that we only match incidents with the same path anomalies. This design specifically resolved **E06** (an incident where logs were spoofed to point to `payment-svc` but traces pointed to `cart-svc -> cart-redis`). Since the trace anomalies did not match the history of pool exhaustion, the trace similarity $S_{trace}$ dropped to 0, preventing a false positive match and allowing the engine to correctly choose `page_oncall`.

---

### 2. How does outcome-weighted voting change the candidate ranking versus a pure-similarity ranking?
In a pure-similarity ranking, an action would be chosen purely because its proposing incident matches closely, regardless of whether that action actually succeeded or failed in the past. Outcome-weighted voting penalizes failed actions ($w = 0.0$) and scales success/partial success ($w = 1.0$ / $w = 0.5$).

**Concrete Demonstration (E05):**
In **E05**, the top 3 neighbors are:
* `INC-2025-07-04` (sim = 0.6385, proposing `restart_pod` with outcome `success` [weight = 1.0])
* `INC-2025-09-05` (sim = 0.4500, proposing `rollback_service` & `increase_pool_size` with outcome `success` [weight = 1.0])
* `INC-2026-05-10` (sim = 0.4500, proposing `rollback_service` with outcome `partial` [weight = 0.5])

If we used pure similarity ranking of the top-1 neighbor, the engine would have selected `restart_pod` (proposed by the closest neighbor at 0.6385). However, by utilizing outcome-weighted voting:
* For `restart_pod` (proposed only by `INC-2025-07-04`):
  * success_rate = $1.0$, max_sim = $0.6385$. Voting score = $1.0 \times 0.6385 = 0.6385$.
* For `increase_pool_size` (proposed only by `INC-2025-09-05`):
  * success_rate = $1.0$, max_sim = $0.4500$. Voting score = $1.0 \times 0.4500 = 0.4500$.
* For `rollback_service` (proposed by `INC-2025-09-05` [success] and `INC-2026-05-10` [partial]):
  * success_sum = $0.4500 \times 1.0 + 0.4500 \times 0.5 = 0.6750$.
  * total_sum = $0.4500 + 0.4500 = 0.9000$.
  * success_rate = $0.6750 / 0.9000 = 0.75$.
  * max_sim = $0.4500$.
  * Voting score = $0.75 \times 0.4500 = 0.3375$.

Without outcome weights (treating `partial` as a full `success` with weight = 1.0), the success rate for `rollback_service` would be 1.0, and its voting score would be 0.4500 (tying with `increase_pool_size`). With outcome-weighted voting, the partial failure is penalized, reducing the voting score of `rollback_service` to 0.3375, allowing `increase_pool_size` to rank higher. Ultimately, because all candidate EVs under this voting distribution fell below the $EV_{page\_oncall} = 25.0$ threshold, the engine safely defaulted to `page_oncall` to avoid executing risky automated actions.

---

### 3. For one eval incident, explain the EV calculation in full
Let's analyze **E01** (Pool exhaustion on `payment-svc`):
The candidate actions and their voting scores from Layer 2:
* `increase_pool_size` (service: `payment-svc`): voting score = 0.8871 (normalized/bounded to $P_{success} = 0.89$).
* `rollback_service` (service: `payment-svc`, target_version: `previous`): voting score = 0.8871 (normalized/bounded to $P_{success} = 0.89$).

**Expected Value (EV) calculation:**
1. **For `increase_pool_size`:**
   * $Cost = cost\_min (1) + 2 \times downtime\_min (0) + 5 \times blast\_radius (1) = 6$.
   * $Utility(success) = 100 - 6 = 94$.
   * $Utility(failure) = -50 - 6 - 0.5 \times 30 = -71$.
   * $EV = 0.89 \times 94 + 0.11 \times (-71) = 83.66 - 7.81 = 75.85$ (computed as 75.37 due to raw float precision).
2. **For `rollback_service`:**
   * $Cost = cost\_min (10) + 2 \times downtime\_min (2) + 5 \times blast\_radius (1) = 19$.
   * $Utility(success) = 100 - 19 = 81$.
   * $Utility(failure) = -50 - 19 - 0.5 \times 60 = -99$.
   * $EV = 0.89 \times 81 + 0.11 \times (-99) = 72.09 - 10.89 = 61.20$.
3. **For `page_oncall`:**
   * Expected utility is fixed as a baseline threshold: $EV_{page\_oncall} = 25.0$.

**Winner:**
`increase_pool_size` won over `rollback_service` and `page_oncall` because it has the highest EV (75.37 vs 61.20 vs 25.00).

---

### 4. When did your engine choose to escalate (page_oncall) instead of auto-act?
Our engine escalated to `page_oncall` in the following scenarios:
* **Out-of-Distribution (OOD) Incidents (E04, E08):** Their maximum similarity to the historical corpus was below the $0.35$ threshold (E04 sim = 0.30, E08 sim = 0.25). The engine correctly escalated immediately rather than guessing.
* **Low-EV / Conflicting Evidence (E05, E06, E07):**
  * In E05 and E06, the expected utility of all automated remediation options fell below the $25.0$ threshold because of low similarities or vote splits, so the engine defaulted to `page_oncall` (which is acceptable and correct in both cases).
  * **E07:** Không phải OOD (max similarity = 0.50 $\ge$ 0.35). Engine đi qua voting nhưng `page_oncall` là action được vote cao nhất với confidence = 0.5. EV = $0.5 \times 100 + 0.5 \times (-50) = 25.0 \le EV_{page\_oncall}$ threshold (25.0), nên engine escalate theo EV-threshold path, không phải OOD path.
* **Manual-Only Issues (E02):** E02 is a TLS certificate rotation issue, which does not have an automated playbook in history, so the only viable path was escalation.
---

### 5. What is the most likely class of incident that breaks your engine?
The most likely class of incident that breaks our engine is a **cascading service outage** where the root cause is a completely new service not seen in the historical corpus, but the cascade manifests through downstream errors that match a historical template on a different service.
* *Example:* If service A fails, causing service B to exhaust its connection pool, the logs will show pool exhaustion on B. A naive similarity match will suggest `increase_pool_size` on B, but the true root cause is B's dependency on A.

**Proposed Improvement:**
To implement a topological root-cause tracer (e.g. using PageRank or eBPF call-graph analysis) that traces the flow of anomalous latency/errors back to the leaf nodes of the dependency graph before looking up matching historical incidents. We did not implement this because building a dynamic call-graph parser and testing it under the constraints of this lab was outside the timeline.
