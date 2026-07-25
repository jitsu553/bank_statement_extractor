"""Bank detection using signature patterns and parser mapping."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List

from core.models import DetectionResult
from core.pdf_loader import extract_first_pages_text
from utils.runtime import resolve_resource_path


@dataclass
class BankSignature:
    key: str
    display_name: str
    patterns: List[str]
    parser: str


def _load_signatures(config_path: str = "config/bank_signatures.json") -> List[BankSignature]:
    path = resolve_resource_path(config_path)
    with open(path, "r", encoding="utf-8") as file:
        raw: Dict[str, Dict[str, object]] = json.load(file)

    signatures: List[BankSignature] = []
    for key, data in raw.items():
        signatures.append(
            BankSignature(
                key=key,
                display_name=str(data.get("display_name", key.upper())),
                patterns=[str(pattern).upper() for pattern in data.get("patterns", [])],
                parser=str(data.get("parser", "generic")),
            )
        )
    return signatures


def detect_bank(pdf_path: str) -> DetectionResult:
    text = extract_first_pages_text(pdf_path, max_pages=2).upper()
    signatures = _load_signatures()

    best_match: DetectionResult | None = None
    best_score = 0.0

    for signature in signatures:
        matched = [pattern for pattern in signature.patterns if pattern in text]
        if not matched:
            continue
        score = len(matched) / max(len(signature.patterns), 1)
        if score > best_score:
            best_score = score
            best_match = DetectionResult(
                bank_key=signature.key,
                bank_display_name=signature.display_name,
                parser_key=signature.parser,
                confidence=score,
                reason=f"Matched signature(s): {', '.join(matched)}",
            )

    if best_match:
        return best_match

    return DetectionResult(
        bank_key="unknown",
        bank_display_name="Unknown Bank",
        parser_key="generic",
        confidence=0.0,
        reason="No known bank signature matched; using generic parser.",
    )


def get_bank_dropdown_values() -> List[str]:
    """Return bank keys exactly as configured for UI dropdown display."""
    signatures = _load_signatures()
    return [signature.key for signature in signatures]


def get_parser_for_bank_key(bank_key: str) -> str:
    """Resolve parser key for a selected bank key; fallback to generic."""
    if not bank_key:
        return "generic"

    signatures = _load_signatures()
    for signature in signatures:
        if signature.key == bank_key:
            return signature.parser
    return "generic"
