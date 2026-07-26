"""Parser registry and factory helpers."""

from __future__ import annotations

from typing import Dict

from parsers.base_parser import BaseParser
from parsers.canara_parser import CanaraParser
from parsers.generic_parser import GenericParser
from parsers.icici_parser import IciciParser


def get_registry() -> Dict[str, BaseParser]:
    generic = GenericParser(key="generic", display_name="generic")
    canara = CanaraParser(key="canara", display_name="canara")
    icici = IciciParser(key="icici", display_name="icici")
    return {
        generic.key: generic,
        canara.key: canara,
        icici.key: icici,
    }


def get_parser(parser_key: str) -> BaseParser:
    normalized = (parser_key or "generic").strip()
    if not normalized:
        normalized = "generic"

    if normalized == "generic":
        return GenericParser(key="generic", display_name="generic")

    if normalized == "canara":
        return CanaraParser(key="canara", display_name="canara")

    if normalized == "icici":
        return IciciParser(key="icici", display_name="icici")

    return GenericParser(key=normalized, display_name=normalized)
