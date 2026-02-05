from __future__ import annotations

import atexit
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Iterable


def _cleanup_stale_logs(log_dir: Path, name_prefix: str, max_age_days: int) -> None:
    if max_age_days <= 0:
        return

    cutoff = max_age_days * 24 * 60 * 60
    now = int(time.time())

    for log_path in log_dir.glob(f"{name_prefix}-*.log"):
        try:
            age = now - int(log_path.stat().st_mtime)
            if age >= cutoff:
                log_path.unlink()
        except Exception:
            continue


def setup_temp_logging(
    name_prefix: str = "semi-utils",
    cleanup_on_start: bool = True,
    cleanup_max_age_days: int = 1,
) -> Path:
    log_dir = Path(tempfile.gettempdir())
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name_prefix}-{os.getpid()}.log"

    if cleanup_on_start:
        _cleanup_stale_logs(log_dir, name_prefix, cleanup_max_age_days)

    handlers = [
        logging.FileHandler(log_path, mode="w", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )

    def _cleanup():
        for handler in handlers:
            try:
                handler.close()
            except Exception:
                pass
        try:
            if log_path.exists():
                os.remove(log_path)
        except Exception:
            pass

    atexit.register(_cleanup)
    return log_path
