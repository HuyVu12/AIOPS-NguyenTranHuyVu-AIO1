"""
cost_model.py — AIOps Platform Cost Estimation
===============================================
Estimates monthly infrastructure cost for 3 scale tiers vs Datadog SaaS.

Architecture Stack (Self-hosted on AWS):
  OTel Collector → Kafka → Flink → VictoriaMetrics + Loki → Grafana
"""

from dataclasses import dataclass, field
from typing import Dict


# ─────────────────────────────────────────────
#  Tier Definitions
# ─────────────────────────────────────────────
@dataclass
class Tier:
    name: str
    services: int
    log_gb_per_day: float        # GB/day
    metrics_events_per_sec: int  # events/sec (metrics)


TIERS = [
    Tier("Small",  services=10,   log_gb_per_day=50,    metrics_events_per_sec=100_000),
    Tier("Medium", services=100,  log_gb_per_day=500,   metrics_events_per_sec=1_000_000),
    Tier("Large",  services=1000, log_gb_per_day=5_120, metrics_events_per_sec=10_000_000),
]


# ─────────────────────────────────────────────
#  AWS Pricing Constants (us-east-1, on-demand)
# ─────────────────────────────────────────────
# EC2
EC2_PRICE = {
    "t3.large":     0.0832,   # 2 vCPU,  8 GB  — OTel Collector, Grafana
    "m6i.xlarge":   0.192,    # 4 vCPU,  16 GB — Kafka broker, Flink TaskManager
    "m6i.2xlarge":  0.384,    # 8 vCPU,  32 GB — Flink JobManager, VictoriaMetrics
    "m6i.4xlarge":  0.768,    # 16 vCPU, 64 GB — Large-tier Kafka/Flink
    "m6i.8xlarge":  1.536,    # 32 vCPU, 128GB — Large-tier compute
}
HOURS_PER_MONTH = 730

# Storage
S3_STORAGE_PER_GB   = 0.023   # $/GB/month (Standard)
EBS_GP3_PER_GB      = 0.08    # $/GB/month
S3_PUT_PER_1K       = 0.005   # per 1000 PUT requests

# Network
DATA_TRANSFER_OUT_PER_GB = 0.09  # AWS egress $/GB (first 10 TB)

# Managed Kafka (MSK) fallback not used — self-hosted Kafka on EC2

# ─────────────────────────────────────────────
#  Datadog SaaS Pricing (public list price)
# ─────────────────────────────────────────────
DD_HOST_PER_MONTH        = 23.0    # $/host/month (Infrastructure Pro)
DD_LOG_INGEST_PER_GB     = 0.10    # $/GB ingested
DD_LOG_RETENTION_PER_GB  = 1.70    # $/GB/month (15-day retention)
DD_APM_HOST_PER_MONTH    = 40.0    # $/APM host/month
DD_METRICS_CUSTOM_PER_100 = 5.0    # $/100 custom metrics/month


# ─────────────────────────────────────────────
#  Cost Calculation: Self-hosted
# ─────────────────────────────────────────────
@dataclass
class CostBreakdown:
    compute:    float = 0.0
    storage:    float = 0.0
    network:    float = 0.0

    @property
    def total(self) -> float:
        return self.compute + self.storage + self.network


def estimate_self_hosted(tier: Tier) -> Dict[str, CostBreakdown]:
    """
    Returns per-component cost breakdown dict for a given tier.
    Components: otel, kafka, flink, victoriametrics, loki, grafana
    """
    log_gb_month     = tier.log_gb_per_day * 30
    metrics_B_per_sec = tier.metrics_events_per_sec

    # ── OTel Collector ───────────────────────────────────────────────────────
    # Rule of thumb: 1 t3.large handles ~200K events/sec; scale horizontally
    otel_instances = max(1, metrics_B_per_sec // 200_000)
    otel = CostBreakdown(
        compute = otel_instances * EC2_PRICE["t3.large"] * HOURS_PER_MONTH,
        storage = otel_instances * 20 * EBS_GP3_PER_GB,   # 20 GB root disk each
        network = 0.0,  # internal VPC traffic, negligible
    )

    # ── Apache Kafka ──────────────────────────────────────────────────────────
    # 3 brokers minimum; scale up instance type by throughput
    # ~1 GB/sec per m6i.xlarge broker; 3-replica factor doubles storage needs
    kafka_throughput_gb_per_sec = (log_gb_month / 30 / 86400) + (metrics_B_per_sec * 200e-9)
    if kafka_throughput_gb_per_sec < 0.5:
        kafka_instance = "m6i.xlarge"
    elif kafka_throughput_gb_per_sec < 2.0:
        kafka_instance = "m6i.2xlarge"
    else:
        kafka_instance = "m6i.4xlarge"

    kafka_brokers   = 3
    kafka_retention_gb = log_gb_month * 3 * (2 / 24)  # 2h retention × replication factor 3
    kafka = CostBreakdown(
        compute = kafka_brokers * EC2_PRICE[kafka_instance] * HOURS_PER_MONTH,
        storage = kafka_retention_gb * EBS_GP3_PER_GB,
        network = 0.0,
    )

    # ── Apache Flink ──────────────────────────────────────────────────────────
    # 1 JobManager + N TaskManagers; 1 TM per 500K events/sec
    flink_tm_count   = max(2, metrics_B_per_sec // 500_000)
    flink_jm_count   = 1
    if tier.name == "Large":
        flink_instance = "m6i.4xlarge"
    elif tier.name == "Medium":
        flink_instance = "m6i.2xlarge"
    else:
        flink_instance = "m6i.xlarge"

    flink = CostBreakdown(
        compute = (flink_jm_count * EC2_PRICE["m6i.xlarge"] +
                   flink_tm_count * EC2_PRICE[flink_instance]) * HOURS_PER_MONTH,
        storage = (flink_jm_count + flink_tm_count) * 50 * EBS_GP3_PER_GB,
        network = 0.0,
    )

    # ── VictoriaMetrics (Metrics TSDB) ────────────────────────────────────────
    # ~2 bytes/sample compressed; 15-day hot retention on EBS, 90d cold on S3
    samples_per_month    = metrics_B_per_sec * 86400 * 30
    vm_hot_storage_gb    = (samples_per_month * 2e-9) * (15 / 30)   # 15-day on EBS
    vm_cold_storage_gb   = (samples_per_month * 2e-9) * (90 / 30)   # 90-day on S3
    if tier.name == "Large":
        vm_instance = "m6i.4xlarge"
    elif tier.name == "Medium":
        vm_instance = "m6i.2xlarge"
    else:
        vm_instance = "m6i.xlarge"

    vm_node_count = max(1, metrics_B_per_sec // 2_000_000)  # VictoriaMetrics is very efficient
    victoriametrics = CostBreakdown(
        compute = vm_node_count * EC2_PRICE[vm_instance] * HOURS_PER_MONTH,
        storage = (vm_hot_storage_gb * EBS_GP3_PER_GB +
                   vm_cold_storage_gb * S3_STORAGE_PER_GB),
        network = 0.0,
    )

    # ── Loki (Log Storage) ────────────────────────────────────────────────────
    # Loki separates compute from storage; logs compressed ~10x → stored on S3
    loki_compressed_gb    = log_gb_month / 10
    loki_index_gb         = loki_compressed_gb * 0.02  # index ~2% of compressed
    loki_hot_days         = 7
    loki_instance = "m6i.xlarge" if tier.name in ("Small", "Medium") else "m6i.2xlarge"
    loki_nodes    = max(1, int(log_gb_month / 86400 / 30))  # 1 node per 1 GB/s ingest

    loki = CostBreakdown(
        compute = loki_nodes * EC2_PRICE[loki_instance] * HOURS_PER_MONTH,
        storage = (loki_compressed_gb * S3_STORAGE_PER_GB +          # S3 object store
                   loki_index_gb * EBS_GP3_PER_GB),                   # EBS index
        network = loki_compressed_gb * DATA_TRANSFER_OUT_PER_GB * 0.1,  # ~10% query egress
    )

    # ── Grafana ───────────────────────────────────────────────────────────────
    grafana_nodes = 1 if tier.name != "Large" else 2
    grafana = CostBreakdown(
        compute = grafana_nodes * EC2_PRICE["t3.large"] * HOURS_PER_MONTH,
        storage = grafana_nodes * 20 * EBS_GP3_PER_GB,
        network = tier.services * 0.5 * DATA_TRANSFER_OUT_PER_GB,  # dashboard queries egress
    )

    return {
        "OTel Collector":   otel,
        "Apache Kafka":     kafka,
        "Apache Flink":     flink,
        "VictoriaMetrics":  victoriametrics,
        "Loki":             loki,
        "Grafana":          grafana,
    }


# ─────────────────────────────────────────────
#  Cost Calculation: Datadog SaaS
# ─────────────────────────────────────────────
def estimate_datadog(tier: Tier) -> dict:
    """
    Returns Datadog cost breakdown dict.
    Components: infrastructure, logs, apm, metrics
    """
    log_gb_month = tier.log_gb_per_day * 30

    infra    = tier.services * DD_HOST_PER_MONTH
    logs     = (log_gb_month * DD_LOG_INGEST_PER_GB +
                log_gb_month * DD_LOG_RETENTION_PER_GB)  # 15-day retention
    apm      = tier.services * DD_APM_HOST_PER_MONTH
    # Custom metrics: assume 100 metrics/service × events mapped to distinct series
    custom_metric_count = tier.services * 100
    metrics  = (custom_metric_count / 100) * DD_METRICS_CUSTOM_PER_100

    return {
        "Infrastructure (hosts)": infra,
        "Log Ingest + Retention":  logs,
        "APM":                     apm,
        "Custom Metrics":          metrics,
    }


# ─────────────────────────────────────────────
#  Pretty-Print Helpers
# ─────────────────────────────────────────────
COL_W = 22

def fmt_usd(v: float) -> str:
    return f"${v:>10,.0f}"


def print_self_hosted_table(tier: Tier, breakdown: Dict[str, CostBreakdown]):
    print(f"\n{'═'*75}")
    print(f"  SELF-HOSTED  |  {tier.name.upper()} TIER"
          f"  ({tier.services} svcs, {tier.log_gb_per_day} GB/day logs,"
          f" {tier.metrics_events_per_sec:,} ev/s metrics)")
    print(f"{'═'*75}")
    print(f"  {'Component':<{COL_W}}  {'Compute':>12}  {'Storage':>12}  {'Network':>12}  {'Total':>12}")
    print(f"  {'-'*COL_W}  {'-'*12}  {'-'*12}  {'-'*12}  {'-'*12}")

    grand = CostBreakdown()
    for name, cb in breakdown.items():
        print(f"  {name:<{COL_W}}  {fmt_usd(cb.compute)}  {fmt_usd(cb.storage)}  {fmt_usd(cb.network)}  {fmt_usd(cb.total)}")
        grand.compute += cb.compute
        grand.storage += cb.storage
        grand.network += cb.network

    print(f"  {'─'*COL_W}  {'─'*12}  {'─'*12}  {'─'*12}  {'─'*12}")
    print(f"  {'TOTAL':<{COL_W}}  {fmt_usd(grand.compute)}  {fmt_usd(grand.storage)}  {fmt_usd(grand.network)}  {fmt_usd(grand.total)}")
    return grand.total


def print_datadog_table(tier: Tier, dd: dict):
    total = sum(dd.values())
    print(f"\n  DATADOG SaaS  |  {tier.name.upper()} TIER")
    print(f"  {'-'*50}")
    for name, cost in dd.items():
        print(f"  {name:<{COL_W+10}}  {fmt_usd(cost)}")
    print(f"  {'─'*50}")
    print(f"  {'TOTAL':<{COL_W+10}}  {fmt_usd(total)}")
    return total


def print_comparison(tier: Tier, self_total: float, dd_total: float):
    ratio   = dd_total / self_total if self_total > 0 else float("inf")
    savings = dd_total - self_total
    print(f"\n  ┌─ BUILD vs BUY — {tier.name.upper()} TIER {'─'*35}┐")
    print(f"  │  Self-hosted monthly cost :  {fmt_usd(self_total)}")
    print(f"  │  Datadog SaaS monthly cost:  {fmt_usd(dd_total)}")
    print(f"  │  Datadog / Self ratio     :  {ratio:>10.1f}x more expensive")
    print(f"  │  Monthly savings (build)  :  {fmt_usd(savings)}")
    print(f"  │  Annual savings (build)   :  {fmt_usd(savings * 12)}")
    verdict = ("BUILD" if savings > 0 else "BUY") + " is cheaper"
    print(f"  │  Verdict                  :  {verdict}")
    print(f"  └{'─'*67}┘")


# ─────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────
def main():
    print("\n" + "█"*75)
    print("  AIOps Platform — Monthly Cost Estimation (AWS us-east-1, June 2026)")
    print("  Stack: OTel → Kafka → Flink → VictoriaMetrics + Loki → Grafana")
    print("█"*75)

    summary_rows = []

    for tier in TIERS:
        self_breakdown = estimate_self_hosted(tier)
        dd_breakdown   = estimate_datadog(tier)

        self_total = print_self_hosted_table(tier, self_breakdown)
        dd_total   = print_datadog_table(tier, dd_breakdown)
        print_comparison(tier, self_total, dd_total)

        summary_rows.append((tier.name, self_total, dd_total))

    # ── Summary across tiers ──────────────────────────────────────────────────
    print(f"\n\n{'═'*75}")
    print("  CROSS-TIER SUMMARY")
    print(f"{'═'*75}")
    print(f"  {'Tier':<10}  {'Self-hosted/mo':>16}  {'Datadog/mo':>16}  {'Ratio':>8}  {'Annual Savings':>16}")
    print(f"  {'─'*10}  {'─'*16}  {'─'*16}  {'─'*8}  {'─'*16}")
    for (tname, st, dt) in summary_rows:
        ratio = dt / st if st > 0 else 0
        print(f"  {tname:<10}  {fmt_usd(st):>16}  {fmt_usd(dt):>16}  {ratio:>7.1f}x  {fmt_usd((dt-st)*12):>16}")
    print(f"{'═'*75}\n")


if __name__ == "__main__":
    main()
