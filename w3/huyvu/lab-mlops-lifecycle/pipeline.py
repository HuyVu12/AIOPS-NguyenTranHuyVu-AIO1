"""pipeline.py — Train IsolationForest with scaling and log/register in MLflow.

Usage:
    python pipeline.py --data data/baseline.csv
    python pipeline.py --data data/baseline.csv --contamination 0.05 --n-estimators 150
"""

import argparse
import os
import pickle
import tempfile
import mlflow
import mlflow.sklearn
import pandas as pd
from mlflow import MlflowClient
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

EXPERIMENT_NAME = "anomaly-detection"
MODEL_NAME = "anomaly-detector"
FEATURES = ["latency_p99", "error_rate", "rps"]

def load_features(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["timestamp"])
    missing = [f for f in FEATURES if f not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in {csv_path}: {missing}")
    return df[FEATURES].dropna()

def train(
    data_path: str,
    contamination: float = 0.03,
    n_estimators: int = 100,
    random_state: int = 42,
) -> None:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(EXPERIMENT_NAME)

    X = load_features(data_path)
    feature_count = X.shape[1]

    # Create sklearn Pipeline to bundle scaler and estimator together.
    # This prevents scaling bugs in deployment/serving.
    scaler = StandardScaler()
    estimator = IsolationForest(
        contamination=contamination,
        n_estimators=n_estimators,
        random_state=random_state,
        n_jobs=-1,
    )
    
    pipeline = Pipeline([
        ("scaler", scaler),
        ("model", estimator)
    ])

    print(f"[pipeline] Training IsolationForest pipeline on {len(X)} rows...")
    pipeline.fit(X)

    # Calculate train anomaly rate (predictions of -1 represent anomalies)
    preds = pipeline.predict(X)
    train_anomaly_rate = float((preds == -1).mean())

    with mlflow.start_run(run_name="train-v1") as run:
        # Log parameters
        mlflow.log_param("contamination", contamination)
        mlflow.log_param("n_estimators", n_estimators)
        mlflow.log_param("random_state", random_state)

        # Log metrics
        mlflow.log_metric("train_anomaly_rate", train_anomaly_rate)
        mlflow.log_metric("feature_count", feature_count)

        # Log scaler separately as an artifact for backward compatibility
        with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
            pickle.dump(scaler, f)
            scaler_path = f.name
        mlflow.log_artifact(scaler_path, artifact_path="scaler")
        os.unlink(scaler_path)

        # Log the full pipeline as the model artifact
        mlflow.sklearn.log_model(
            sk_model=pipeline,
            artifact_path="model",
            registered_model_name=MODEL_NAME,
            input_example=X.head(3),
        )

        run_id = run.info.run_id
        print(f"[pipeline] Run ID     : {run_id}")
        print(f"[pipeline] Anomaly rate: {train_anomaly_rate:.4f}")

    # Set alias 'production' on the newly registered model version
    client = MlflowClient(tracking_uri=tracking_uri)
    versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    latest = max(versions, key=lambda v: int(v.version))

    client.set_registered_model_alias(MODEL_NAME, "production", latest.version)
    print(f"[pipeline] Registered  : {MODEL_NAME} v{latest.version} → alias 'production'")
    print(f"[pipeline] MLflow UI   : {tracking_uri}/#/models/{MODEL_NAME}")

def main() -> None:
    parser = argparse.ArgumentParser(description="Train anomaly detection model and log to MLflow")
    parser.add_argument("--data", required=True, help="Path to training CSV data (e.g. data/baseline.csv)")
    parser.add_argument("--contamination", type=float, default=0.03, help="Contamination rate for IsolationForest")
    parser.add_argument("--n-estimators", type=int, default=100, help="Number of estimators for IsolationForest")
    parser.add_argument("--random-state", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    train(
        data_path=args.data,
        contamination=args.contamination,
        n_estimators=args.n_estimators,
        random_state=args.random_state,
    )

if __name__ == "__main__":
    main()
