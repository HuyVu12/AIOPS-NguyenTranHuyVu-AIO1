import math

def is_worth_it(
    num_services: int,
    incidents_per_month: int,
    avg_incident_duration_hours: float,
    downtime_cost_per_hour: float,
    expected_mttr_reduction_pct: float = 0.4,
    aiops_monthly_cost: float = 15_000,
) -> dict:
    """
    Break-even cost model calculator for AIOps platform.
    
    Formula:
      monthly_downtime_hours = incidents_per_month * avg_incident_duration_hours
      monthly_value = monthly_downtime_hours * expected_mttr_reduction_pct * downtime_cost_per_hour
      roi = monthly_value / aiops_monthly_cost
      payback_months = aiops_monthly_cost / monthly_value
      
    Verdict:
      roi > 1.5        -> worth_it
      1.0 < roi <= 1.5 -> marginal
      roi <= 1.0       -> not_worth_it
    """
    monthly_downtime_hours = incidents_per_month * avg_incident_duration_hours
    monthly_value = (
        monthly_downtime_hours
        * expected_mttr_reduction_pct
        * downtime_cost_per_hour
    )
    
    if aiops_monthly_cost > 0:
        roi = monthly_value / aiops_monthly_cost
    else:
        roi = float('inf')
        
    if monthly_value > 0:
        payback_months = aiops_monthly_cost / monthly_value
    else:
        payback_months = float('inf')
        
    if roi > 1.5:
        verdict = "worth_it"
    elif 1.0 < roi <= 1.5:
        verdict = "marginal"
    else:
        verdict = "not_worth_it"
        
    return {
        "monthly_value": float(monthly_value),
        "monthly_cost": float(aiops_monthly_cost),
        "roi": float(roi),
        "payback_months": float(payback_months),
        "verdict": verdict
    }

if __name__ == "__main__":
    # Scenario 1 (Notes): 20 services, 2 incidents/mo, avg 1h duration, $10k/h downtime cost, $15k AIOps cost
    # ROI = (2 * 1 * 0.4 * 10000) / 15000 = 8000 / 15000 = 0.53 -> not_worth_it
    print("Scenario 1:")
    res1 = is_worth_it(
        num_services=20,
        incidents_per_month=2,
        avg_incident_duration_hours=1,
        downtime_cost_per_hour=10_000,
        aiops_monthly_cost=15_000
    )
    print(res1)
    
    # Scenario 2 (Notes): 100 services, 5 incidents/mo, avg 2h duration, $20k/h downtime cost, $25k AIOps cost
    # ROI = (5 * 2 * 0.4 * 20000) / 25000 = 80000 / 25000 = 3.2 -> worth_it
    print("\nScenario 2:")
    res2 = is_worth_it(
        num_services=100,
        incidents_per_month=5,
        avg_incident_duration_hours=2,
        downtime_cost_per_hour=20_000,
        aiops_monthly_cost=25_000
    )
    print(res2)
    
    # Scenario 3 (Custom - Mid-tier E-commerce): 50 services, 3 incidents/mo, avg 1.5h duration, $12k/h downtime cost, $15k AIOps cost
    # ROI = (3 * 1.5 * 0.4 * 12000) / 15000 = 21600 / 15000 = 1.44 -> marginal
    # Defending downtime cost: For a mid-tier e-commerce platform, downtime translates directly to lost cart transactions
    # and cart abandonment. Based on ITIC 2024 survey, hourly downtime cost for mid-tier ranges between $5,000 and $50,000.
    # We choose a realistic baseline of $12,000/hour.
    print("\nScenario 3 (Custom - Mid-tier E-commerce):")
    res3 = is_worth_it(
        num_services=50,
        incidents_per_month=3,
        avg_incident_duration_hours=1.5,
        downtime_cost_per_hour=12_000,
        aiops_monthly_cost=15_000
    )
    print(res3)
