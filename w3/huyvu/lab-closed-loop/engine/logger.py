"""
engine/logger.py — Structured JSON logger for the closed-loop orchestrator.

Emits structured JSON records to stdout AND appends every event to an
audit log file (JSONL) so Grafana/Loki can tail it.

Environment variable:
    AUDIT_LOG_PATH — path to the audit log file (default: audit_log.jsonl)
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_AUDIT_PATH = os.environ.get("AUDIT_LOG_PATH", "audit_log.jsonl")
_audit_lock = threading.Lock()


def _write_audit(record: dict) -> None:
    """Append a JSON record to the audit log file (thread-safe)."""
    try:
        with _audit_lock:
            with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
    except Exception:
        pass  # Never let logging break the orchestrator


class JsonLogger:
    """Emit structured JSON log records to stdout (and audit log)."""

    def __init__(self, name: str):
        self._name = name

    def _emit(self, level: str, event_type: str, **kwargs) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "logger": self._name,
            "event_type": event_type,
            **kwargs,
        }
        print(json.dumps(record), flush=True)
        _write_audit(record)

    def info(self, event_type: str, **kwargs) -> None:
        self._emit("INFO", event_type, **kwargs)

    def warning(self, event_type: str, **kwargs) -> None:
        self._emit("WARNING", event_type, **kwargs)

    def error(self, event_type: str, **kwargs) -> None:
        self._emit("ERROR", event_type, **kwargs)
