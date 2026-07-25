"""XLSX writer preserving source header names and values."""

from __future__ import annotations

from typing import Dict, List

from openpyxl import Workbook


def write_xlsx(output_path: str, headers: List[str], rows: List[Dict[str, str]]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Statement"

    worksheet.append(headers)
    for row in rows:
        worksheet.append([row.get(header, "") for header in headers])

    workbook.save(output_path)
