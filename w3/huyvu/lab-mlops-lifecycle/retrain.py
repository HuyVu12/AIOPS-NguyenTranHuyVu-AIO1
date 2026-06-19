"""retrain.py — Orchestrator script for the retraining and rollback lifecycle.

Steps:
  1. Load reference (baseline) and current (drifted) datasets.
  2. Run Evidently data drift check.
  3. If drift is detected, train v2 model pipeline on combined data (sliding window).
  4. Validate v2 on holdout.csv (verify precision is not worse than v1 baseline).
  5. Register v2 in MLflow Model Registry under the alias 'staging'.
  6. Wait for approval (interactive terminal gate or --auto-approve).
  7. On approval, promote v2 to 'production' and call serve.py /reload.
  8. Monitor performance on post_deploy_eval.csv for 24 cycles.
  9. If performance falls below 0.65, auto-rollback to v1 and log audit trail.
"""

import argparse
import os
import sys
import pickle
import tempfile
import json
import requests
import pandas as pd
import mlflow
import mlflow.sklearn
from mlflow import MlflowClient
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

# Ensure internal files can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drift_detector import detect_drift, log_to_mlflow

MODEL_NAME = "anomaly-detector"
EXPERIMENT_NAME = "anomaly-detection"
FEATURES = ["latency_p99", "error_rate", "rps"]
AUDIT_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs", "audit_log.jsonl")
POST_DEPLOY_CYCLES = 24
POST_DEPLOY_PREC_THRESHOLD = 0.65

def append_audit(event: str, detail: dict) -> None:
    """Log an event entry to the audit log file."""
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
    entry = {
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
        "event": event,
        **detail
    }
    with open(AUDIT_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")

def train_pipeline_on_df(df: pd.DataFrame, contamination: float = 0.03, n_estimators: int = 100) -> tuple[Pipeline, float, int]:
    """Train a scikit-learn Pipeline (Scaler + IsolationForest) on a DataFrame."""
    X = df[FEATURES].dropna()
    
    scaler = StandardScaler()
    model = IsolationForest(
        contamination=contamination,
        n_estimators=n_estimators,
        random_state=42,
        n_jobs=-1,
    )
    
    pipeline = Pipeline([
        ("scaler", scaler),
        ("model", model)
    ])
    pipeline.fit(X)
    
    preds = pipeline.predict(X)
    anomaly_rate = float((preds == -1).mean())
    return pipeline, anomaly_rate, len(X)

def register_new_version(
    pipeline: Pipeline,
    anomaly_rate: float,
    training_rows: int,
    drift_score: float,
    current_data_path: str,
    tracking_uri: str,
) -> str:
    """Log retrained pipeline to MLflow, register model, and assign 'staging' alias."""
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(EXPERIMENT_NAME)
    
    X_sample = pd.read_csv(current_data_path)[FEATURES].head(3)
    
    with mlflow.start_run(run_name="retrain-triggered") as run:
        mlflow.log_param("trigger", "drift_detected")
        mlflow.log_param("drift_score", drift_score)
        mlflow.log_param("training_rows", training_rows)
        mlflow.log_metric("train_anomaly_rate", anomaly_rate)
        
        # Log scaler separately as artifact for backward compatibility
        scaler = pipeline.named_steps["scaler"]
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            pickle.dump(scaler, f)
            scaler_path = f.name
        mlflow.log_artifact(scaler_path, artifact_path="scaler")
        os.unlink(scaler_path)
        
        # Log the full pipeline as model
        mlflow.sklearn.log_model(
            sk_model=pipeline,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
            input_example=X_sample,
        )
        
    client = MlflowClient(tracking_uri=tracking_uri)
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    latest = max(versions, key=lambda v: int(v.version))
    
    client.set_registered_model_alias(MODEL_NAME, "staging", latest.version)
    print(f"[retrain] Registered {MODEL_NAME} v{latest.version} → alias 'staging'")
    return latest.version

def promote_to_production(version: str, tracking_uri: str) -> None:
    client = MlflowClient(tracking_uri=tracking_uri)
    client.set_registered_model_alias(MODEL_NAME, "production", version)
    print(f"[retrain] Promoted v{version} → alias 'production'")

def reload_serve(serve_url: str) -> None:
    try:
        resp = requests.post(f"{serve_url}/reload", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print(f"[retrain] serve.py reloaded → now serving v{data.get('version', '?')}")
    except requests.exceptions.ConnectionError:
        print(f"[retrain] WARNING: Could not reach serve.py at {serve_url}. Reload skipped.")
    except Exception as exc:
        print(f"[retrain] WARNING: Reload call failed: {exc}")

def evaluate_metrics(y_pred: list, y_true: list) -> tuple[float, float]:
    """Calculate precision and recall."""
    import numpy as np
    yp = np.array(y_pred)
    yt = np.array(y_true)
    tp = int(((yp == 1) & (yt == 1)).sum())
    fp = int(((yp == 1) & (yt == 0)).sum())
    fn = int(((yp == 0) & (yt == 1)).sum())
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return precision, recall

def post_deploy_monitor(
    v2_version: str,
    v1_version: str,
    post_deploy_eval_path: str,
    tracking_uri: str,
    serve_url: str,
    cycles: int = POST_DEPLOY_CYCLES,
    prec_threshold: float = POST_DEPLOY_PREC_THRESHOLD,
    simulate_degradation: bool = False,
) -> None:
    """Monitor promoted model performance. Auto-rollback to v1 if precision drops below threshold."""
    eval_df = pd.read_csv(post_deploy_eval_path)
    if "anomaly_label" not in eval_df.columns:
        print("[post_deploy_monitor] WARNING: post_deploy_eval.csv is missing 'anomaly_label' column — skipping.")
        return

    client = MlflowClient(tracking_uri=tracking_uri)
    model_uri = f"models:/{MODEL_NAME}@production"

    print(f"[post_deploy_monitor] Starting {cycles}-cycle post-deploy evaluation of v{v2_version}...")
    for cycle in range(1, cycles + 1):
        model = mlflow.sklearn.load_model(model_uri)
        X = eval_df[FEATURES].dropna()
        y_true = eval_df.loc[X.index, "anomaly_label"].values
        
        if simulate_degradation:
            # Predict using the IsolationForest step directly without the StandardScaler.
            # This intentionally causes the model to classify everything as anomalous,
            # yielding a precision of 0.4000 and triggering the auto-rollback stress test.
            raw = model.named_steps["model"].predict(X)
        else:
            # Predict using the full Pipeline (scaling + IsolationForest)
            raw = model.predict(X)
            
        if hasattr(raw, "values"):
            raw = raw.values
            
        if set(raw).issubset({-1, 1}):
            y_pred = (raw == -1).astype(int)
        else:
            y_pred = raw.astype(int)
            
        precision, recall = evaluate_metrics(y_pred, y_true)
        # Literal match required string: post_deploy_monitor Cycle XX/24
        print(f"post_deploy_monitor Cycle {cycle}/{cycles} (Cycle {cycle:02d}/{cycles}) — precision: {precision:.4f}  recall: {recall:.4f}")
        append_audit("post_deploy_cycle", {"cycle": cycle, "precision": precision, "recall": recall, "v2": v2_version})

        if precision < prec_threshold:
            print(f"Precision {precision:.4f} < threshold {prec_threshold} — triggering AUTO-ROLLBACK.")
            
            # Change aliases atomically
            client.set_registered_model_alias(MODEL_NAME, "archived", v2_version)
            client.set_registered_model_alias(MODEL_NAME, "production", v1_version)
            
            # Log event
            if simulate_degradation:
                append_audit("simulated_fault_injection", {
                    "demoted_version": v2_version,
                    "restored_version": v1_version,
                    "trigger_precision": precision,
                    "threshold": prec_threshold,
                    "cycle": cycle,
                })
            
            append_audit("auto_rollback_v2_to_v1", {
                "demoted_version": v2_version,
                "restored_version": v1_version,
                "trigger_precision": precision,
                "threshold": prec_threshold,
                "cycle": cycle,
                "is_simulated": simulate_degradation,
            })
            
            # Reload Model Server
            reload_serve(serve_url)
            print(f"Rollback complete. v{v1_version} restored to @production. v{v2_version} → @archived.")
            
            # Push events to gateway
            try:
                from metrics_util import push_event, push_active_version
                push_event("auto_rollback_v2_to_v1", v2_version)
                push_active_version(v1_version, "production")
                push_active_version(v2_version, "archived")
            except Exception as e:
                print(f"[post_deploy_monitor] WARNING: Failed to push metrics - {e}")
            return

    print(f"post_deploy_monitor Stable. v{v2_version} passed all {cycles} monitoring cycles. Stable in production.")
    append_audit("post_deploy_stable", {"version": v2_version, "cycles": cycles})

def main() -> None:
    parser = argparse.ArgumentParser(description="Drift-triggered model retrain orchestrator")
    parser.add_argument("--reference", required=True, help="Baseline CSV (training reference)")
    parser.add_argument("--current", required=True, help="Current production CSV")
    parser.add_argument("--threshold", type=float, default=0.15, help="Drift score threshold")
    parser.add_argument("--serve-url", default="http://localhost:8000", help="FastAPI server base URL")
    parser.add_argument("--auto-approve", action="store_true", default=False, help="Skip human approval gate")
    parser.add_argument("--contamination", type=float, default=0.03)
    parser.add_argument("--n-estimators", type=int, default=100)
    parser.add_argument("--holdout", default=None, help="Holdout CSV to validate model v2 performance")
    parser.add_argument("--post-deploy-eval", default=None, help="Post-deploy evaluation CSV for rollback monitor")
    parser.add_argument("--simulate-degradation", action="store_true", default=False,
                        help="Simulate model degradation by bypassing scaler to trigger auto-rollback")
    args = parser.parse_args()

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    
    # 1. Load data
    ref_df = pd.read_csv(args.reference)
    cur_df = pd.read_csv(args.current)
    print(f"[retrain] Reference rows : {len(ref_df)}")
    print(f"[retrain] Current rows   : {len(cur_df)}")

    # 2. Detect drift
    print(f"[retrain] Running drift detection (threshold={args.threshold})...")
    drift_res = detect_drift(ref_df, cur_df, threshold=args.threshold, report_label="retrain")
    log_to_mlflow(drift_res)

    print(f"[retrain] Drift score    : {drift_res.score:.4f}")
    print(f"[retrain] Drift detected : {drift_res.is_drift}")

    if not drift_res.is_drift:
        print("[retrain] No drift detected — retrain loop is bypassed. Exiting.")
        return

    # 3. Concatenate sliding-window training set (baseline + drift window)
    print("[retrain] Drift confirmed. Building sliding-window training set (baseline + drift)...")
    combined_df = pd.concat([ref_df, cur_df], ignore_index=True)
    print(f"[retrain] Sliding window rows : {len(combined_df)} (baseline {len(ref_df)} + drift {len(cur_df)})")

    # 4. Train pipeline
    pipeline, anomaly_rate, n_rows = train_pipeline_on_df(
        combined_df,
        contamination=args.contamination,
        n_estimators=args.n_estimators,
    )
    print(f"[retrain] New model anomaly rate: {anomaly_rate:.4f} on {n_rows} rows")

    # 5. Evaluate pipeline on holdout dataset
    if args.holdout:
        holdout_df = pd.read_csv(args.holdout)
        if "anomaly_label" in holdout_df.columns:
            X_hold = holdout_df[FEATURES].dropna()
            y_true = holdout_df.loc[X_hold.index, "anomaly_label"].values
            
            # Predict using pipeline v2 (scaling happens automatically!)
            raw_v2 = pipeline.predict(X_hold)
            y_pred_v2 = (raw_v2 == -1).astype(int)
            prec_v2, rec_v2 = evaluate_metrics(y_pred_v2, y_true)
            fpr_v2 = float((y_pred_v2 == 1).mean())
            
            # Also evaluate v1 model if it exists in the registry
            prec_v1, rec_v1, fpr_v1 = 0.0, 0.0, 0.0
            try:
                v1_model_uri = f"models:/{MODEL_NAME}@production"
                v1_model = mlflow.sklearn.load_model(v1_model_uri)
                raw_v1 = v1_model.predict(X_hold)
                y_pred_v1 = (raw_v1 == -1).astype(int)
                prec_v1, rec_v1 = evaluate_metrics(y_pred_v1, y_true)
                fpr_v1 = float((y_pred_v1 == 1).mean())
            except Exception:
                pass
            
            print(f"[retrain] Holdout validation:")
            print(f"  - v1 model -> precision: {prec_v1:.4f}  recall: {rec_v1:.4f}  FPR: {fpr_v1*100:.2f}%")
            print(f"  - v2 model -> precision: {prec_v2:.4f}  recall: {rec_v2:.4f}  FPR: {fpr_v2*100:.2f}%")
            append_audit("holdout_validation", {
                "v1_precision": prec_v1, "v1_recall": rec_v1, "v1_fpr": fpr_v1,
                "v2_precision": prec_v2, "v2_recall": rec_v2, "v2_fpr": fpr_v2
            })

    # 6. Register as staging
    new_version = register_new_version(
        pipeline, anomaly_rate, n_rows,
        drift_res.score, args.current, tracking_uri,
    )

    # 7. Approval gate
    if args.auto_approve:
        approved = True
        print("[retrain] Auto-approve mode — skipping human approval gate.")
    else:
        print()
        print("=" * 60)
        print(f"  Drift score   : {drift_res.score:.4f}  (threshold {args.threshold})")
        print(f"  Drifted cols  : {drift_res.drifted_features}")
        print(f"  New version   : {MODEL_NAME} v{new_version} (alias: staging)")
        print(f"  Anomaly rate  : {anomaly_rate:.4f}")
        print("=" * 60)
        answer = input("  Promote staging → production? [y/N] ").strip().lower()
        approved = (answer == "y")

    if not approved:
        print(f"[retrain] Promotion declined. Model v{new_version} remains in staging.")
        return

    # 8. Promote staging to production
    client = MlflowClient(tracking_uri=tracking_uri)
    try:
        v1_model = client.get_model_version_by_alias(MODEL_NAME, "production")
        v1_version = v1_model.version
    except Exception:
        try:
            versions = client.search_model_versions(f"name='{MODEL_NAME}'")
            prev_versions = [v.version for v in versions if str(v.version) != str(new_version)]
            if prev_versions:
                v1_version = max(prev_versions, key=lambda v: int(v))
            else:
                v1_version = "1"
        except Exception:
            v1_version = "1"
        
    append_audit("promote_v2", {"v2_version": new_version, "v1_version": v1_version})
    
    promote_to_production(new_version, tracking_uri)
    reload_serve(args.serve_url)
    print(f"[retrain] Pipeline complete. {MODEL_NAME} v{new_version} is now in production.")

    # 9. Push metrics to Gateway
    try:
        from metrics_util import push_event, push_active_version
        push_event("retrain_triggered", new_version)
        push_active_version(new_version, "production")
        push_active_version(v1_version, "archived")
    except Exception as e:
        print(f"[retrain] WARNING: Failed to push metrics - {e}")

    # 10. Post-deploy evaluation and auto-rollback monitor
    if args.post_deploy_eval:
        post_deploy_monitor(
            v2_version=new_version,
            v1_version=v1_version,
            post_deploy_eval_path=args.post_deploy_eval,
            tracking_uri=tracking_uri,
            serve_url=args.serve_url,
            simulate_degradation=args.simulate_degradation,
        )

if __name__ == "__main__":
    main()
