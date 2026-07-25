"""Shared data models for conversion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ParseResult:
    headers: List[str]
    rows: List[Dict[str, str]]
    warnings: List[str] = field(default_factory=list)
    parser_name: str = "unknown"


@dataclass
class DetectionResult:
    bank_key: str
    bank_display_name: str
    parser_key: str
    confidence: float
    reason: str
