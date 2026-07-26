"""PDF intake and extraction helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List

import pdfplumber


@dataclass
class PageExtraction:
    page_number: int
    text: str
    tables: List[List[List[str]]]
    # Per-word position data (text/x0/x1/top), for parsers that need to tell
    # apart same-shaped columns (e.g. two amount columns) by table position
    # rather than by guessing from plain text alone. Empty unless requested.
    words: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PdfMetadata:
    page_count: int


def get_pdf_metadata(pdf_path: str) -> PdfMetadata:
    with pdfplumber.open(pdf_path) as pdf:
        return PdfMetadata(page_count=len(pdf.pages))


def extract_first_pages_text(pdf_path: str, max_pages: int = 2) -> str:
    snippets: List[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        upper = min(max_pages, len(pdf.pages))
        for i in range(upper):
            text = pdf.pages[i].extract_text() or ""
            snippets.append(text)
    return "\n".join(snippets)


def iterate_pages(pdf_path: str, start_page: int = 0) -> Iterator[PageExtraction]:
    """Yield extracted text, tables, and word positions per page to support large PDFs."""
    with pdfplumber.open(pdf_path) as pdf:
        for index in range(start_page, len(pdf.pages)):
            page = pdf.pages[index]
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            words = [
                {"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"]}
                for w in page.extract_words(use_text_flow=False, keep_blank_chars=False)
            ]
            yield PageExtraction(page_number=index + 1, text=text, tables=tables, words=words)
