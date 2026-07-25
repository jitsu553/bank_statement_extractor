"""Base parser contract for bank statement extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from core.models import ParseResult
from core.pdf_loader import PageExtraction


class BaseParser(ABC):
    key: str = "base"
    display_name: str = "Base Parser"

    @abstractmethod
    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        """Parse the page iterator and return headers and row values."""
        raise NotImplementedError
