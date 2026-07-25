"""PDF intake and extraction helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List

import pdfplumber


@dataclass
class PageExtraction:
    page_number: int
    text: str
    tables: List[List[List[str]]]


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
    """Yield extracted text and tables per page to support large PDFs."""
    with pdfplumber.open(pdf_path) as pdf:
        for index in range(start_page, len(pdf.pages)):
            page = pdf.pages[index]
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            yield PageExtraction(page_number=index + 1, text=text, tables=tables)
