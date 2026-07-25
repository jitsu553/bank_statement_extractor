"""Logging setup and run-scoped helpers."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from utils.runtime import get_runtime_dir

LOGGER_NAME = "bank_statement_extractor"


def _log_path() -> str:
    return os.path.join(get_runtime_dir(), "log.txt")


def setup_logger() -> logging.Logger:
    """Configure a process-wide logger writing to log.txt near the executable."""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(_log_path(), mode="a", encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def start_run_log(logger: logging.Logger, pdf_path: str, output_path: str) -> None:
    logger.info("=" * 80)
    logger.info("Run started")
    logger.info("PDF: %s", pdf_path)
    logger.info("Output: %s", output_path)
    logger.info("Started at: %s", datetime.now().isoformat(timespec="seconds"))


def finish_run_log(
    logger: logging.Logger,
    status: str,
    bank_name: str,
    parser_name: str,
    page_count: Optional[int],
    row_count: int,
    warning_count: int,
) -> None:
    logger.info("Status: %s", status)
    logger.info("Detected bank: %s", bank_name)
    logger.info("Parser: %s", parser_name)
    logger.info("Pages: %s", page_count if page_count is not None else "unknown")
    logger.info("Rows exported: %s", row_count)
    logger.info("Warnings: %s", warning_count)
    logger.info("Finished at: %s", datetime.now().isoformat(timespec="seconds"))
    logger.info("Run ended")
