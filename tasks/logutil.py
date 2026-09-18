"""
logutil.py — shared file+console logging setup for Ploomber tasks.  Import-safe library.

Tasks run on a server; a per-task log file makes progress easy to follow (the notebooks
are for examining results, not for tracking a long run). Each task calls `setup_logging`
with a path inside its product directory.
"""
from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(log_path, level: int = logging.INFO) -> logging.Logger:
    """Configure root logging to write to both `log_path` and the console.

    Uses `force=True` so it overrides any prior basicConfig in the same process
    (Ploomber may import several tasks). Returns the root logger.
    """
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    logging.getLogger().info(f"logging to {log_path}")
    return logging.getLogger()
