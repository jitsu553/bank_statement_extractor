"""Generic parser for unknown or unsupported bank layouts."""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser


class GenericParser(BaseParser):
    key = "generic"
    display_name = "Generic Table Parser"

    def __init__(self, key: str = "generic", display_name: str = "generic") -> None:
        self.key = key
        self.display_name = display_name

    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        headers: List[str] = []
        rows: List[Dict[str, str]] = []
        warnings: List[str] = []

        for page in pages:
            for table in page.tables:
                cleaned = self._clean_table(table)
                if not cleaned:
                    continue

                if not headers:
                    headers = cleaned[0]
                    data_rows = cleaned[1:]
                else:
                    data_rows = cleaned

                for data_row in data_rows:
                    if not self._looks_like_data_row(data_row):
                        continue
                    row_dict = {
                        headers[index]: (data_row[index] if index < len(data_row) else "")
                        for index in range(len(headers))
                    }
                    rows.append(row_dict)

        if not headers:
            warnings.append("No table headers found in PDF. Output file will be empty.")
        return ParseResult(headers=headers, rows=rows, warnings=warnings, parser_name=self.display_name)

    @staticmethod
    def _clean_table(table: Sequence[Sequence[str]]) -> List[List[str]]:
        cleaned: List[List[str]] = []
        for raw_row in table:
            normalized = [((cell or "").strip()) for cell in raw_row]
            if any(normalized):
                cleaned.append(normalized)
        return cleaned

    @staticmethod
    def _looks_like_data_row(row: Sequence[str]) -> bool:
        non_empty = [cell for cell in row if cell.strip()]
        return len(non_empty) >= 2
