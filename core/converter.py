"""End-to-end conversion orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from core.bank_detector import detect_bank
from core.models import DetectionResult, ParseResult
from core.pdf_loader import get_pdf_metadata, iterate_pages
from core.xlsx_writer import write_xlsx
from parsers.registry import get_parser
from utils.logging_utils import finish_run_log, start_run_log


@dataclass
class ConversionResult:
    success: bool
    message: str
    detection: DetectionResult
    parse_result: ParseResult
    page_count: int


ProgressCallback = Optional[Callable[[int, int], None]]


def convert_pdf_to_xlsx(
    pdf_path: str,
    output_xlsx_path: str,
    logger: logging.Logger,
    forced_parser_key: Optional[str] = None,
    progress_callback: ProgressCallback = None,
) -> ConversionResult:
    start_run_log(logger, pdf_path, output_xlsx_path)
    metadata = get_pdf_metadata(pdf_path)

    detection = detect_bank(pdf_path)
    parser_key = forced_parser_key or detection.parser_key
    parser = get_parser(parser_key)

    logger.info("Detector reason: %s", detection.reason)
    logger.info("Using parser key: %s", parser_key)

    page_stream = iterate_pages(pdf_path)

    # Wrap stream so we can push progress without storing full document in memory.
    def progress_stream():
        for index, page in enumerate(page_stream, start=1):
            if progress_callback:
                progress_callback(index, metadata.page_count)
            yield page

    parse_result = parser.parse(progress_stream())

    if not parse_result.headers:
        finish_run_log(
            logger=logger,
            status="failed",
            bank_name=detection.bank_display_name,
            parser_name=parse_result.parser_name,
            page_count=metadata.page_count,
            row_count=0,
            warning_count=len(parse_result.warnings),
        )
        return ConversionResult(
            success=False,
            message="Could not detect a table header row. No XLSX created.",
            detection=detection,
            parse_result=parse_result,
            page_count=metadata.page_count,
        )

    write_xlsx(output_xlsx_path, parse_result.headers, parse_result.rows)

    finish_run_log(
        logger=logger,
        status="success",
        bank_name=detection.bank_display_name,
        parser_name=parse_result.parser_name,
        page_count=metadata.page_count,
        row_count=len(parse_result.rows),
        warning_count=len(parse_result.warnings),
    )

    return ConversionResult(
        success=True,
        message=f"Exported {len(parse_result.rows)} rows to {output_xlsx_path}",
        detection=detection,
        parse_result=parse_result,
        page_count=metadata.page_count,
    )
