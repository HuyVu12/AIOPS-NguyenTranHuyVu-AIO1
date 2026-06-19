"""serve.py — FastAPI model serving with blue-green support.

Loads model from MLflow Registry alias 'production' at startup.
Exposes endpoints:
  POST /predict               - predict anomalies and scores for a batch of features
  GET  /health/active-version - return active model version details
  POST /reload                - reload model from registry without downtime
  GET  /metrics               - expose Prometheus metrics
"""

import argparse
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import mlflow
import mlflow.sklearn
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel

# Prometheus metrics setup
_serve_requests = Counter("serve_requests_total", "Total predict requests received")
_serve_latency = Histogram(
    "serve_predict_latency_seconds",
    "Predict endpoint latency in seconds",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
_serve_active_version = Gauge("serve_active_version", "Currently served model version number")

MODEL_NAME = "anomaly-detector"
MODEL_URI = f"models:/{MODEL_NAME}@production"
FEATURES = ["latency_p99", "error_rate", "rps"]

# Global model state
_state: dict[str, Any] = {
    "pipeline": None,
    "version": None,
    "model_uri": None,
}

def _load_model() -> None:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    mlflow.set_tracking_uri(tracking_uri)

    client = mlflow.MlflowClient(tracking_uri=tracking_uri)
    alias_mv = client.get_model_version_by_alias(MODEL_NAME, "production")

    # Load the scikit-learn Pipeline (which contains StandardScaler + IsolationForest)
    pipeline = mlflow.sklearn.load_model(MODEL_URI)
    
    _state["pipeline"] = pipeline
    _state["version"] = alias_mv.version
    _state["model_uri"] = MODEL_URI
    
    print(f"[serve] Loaded {MODEL_NAME} v{alias_mv.version} from alias 'production'")
    try:
        _serve_active_version.set(int(alias_mv.version))
    except (ValueError, TypeError):
        pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_model()
    yield
    _state["pipeline"] = None

app = FastAPI(title="Anomaly Detector API", lifespan=lifespan)

class PredictRequest(BaseModel):
    # Features format: [[latency_p99, error_rate, rps], ...]
    features: list[list[float]]

class PredictResponse(BaseModel):
    predictions: list[int]      # -1 = anomaly, 1 = normal
    scores: list[float]         # raw anomaly scores (more negative = more anomalous)
    version: str
    model_name: str

class VersionResponse(BaseModel):
    model_name: str
    version: str
    alias: str
    model_uri: str

@app.get("/metrics")
def metrics():
    """Expose Prometheus metrics for scraping."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    if _state["pipeline"] is None:
        raise HTTPException(status_code=503, detail="Model is not loaded")
    if not req.features:
        raise HTTPException(status_code=422, detail="features must not be empty")

    X = np.array(req.features)
    if X.shape[1] != len(FEATURES):
        raise HTTPException(
            status_code=422,
            detail=f"Expected {len(FEATURES)} features per row ({FEATURES}), got {X.shape[1]}",
        )

    _serve_requests.inc()
    t0 = time.perf_counter()
    
    # Predict directly using the Pipeline (scaling + isolation forest)
    predictions = _state["pipeline"].predict(X).tolist()
    scores = _state["pipeline"].score_samples(X).tolist()
    
    _serve_latency.observe(time.perf_counter() - t0)

    return PredictResponse(
        predictions=predictions,
        scores=scores,
        version=str(_state["version"]),
        model_name=MODEL_NAME,
    )

@app.get("/health/active-version", response_model=VersionResponse)
def active_version():
    if _state["pipeline"] is None:
        raise HTTPException(status_code=503, detail="Model is not loaded")
    return VersionResponse(
        model_name=MODEL_NAME,
        version=str(_state["version"]),
        alias="production",
        model_uri=str(_state["model_uri"]),
    )

@app.post("/reload")
def reload():
    """Reload the production model from Registry dynamically."""
    try:
        _load_model()
        return {"status": "reloaded", "version": str(_state["version"])}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

def main() -> None:
    parser = argparse.ArgumentParser(description="Run anomaly detector serving server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload-on-start", action="store_true", default=False)
    args = parser.parse_args()

    # Pass the actual app object directly to uvicorn.run instead of "serve:app" string
    # unless reload-on-start is explicitly requested. This avoids the duplicate timeseries error.
    if args.reload_on_start:
        uvicorn.run("serve:app", host=args.host, port=args.port, reload=True)
    else:
        uvicorn.run(app, host=args.host, port=args.port)

if __name__ == "__main__":
    main()
