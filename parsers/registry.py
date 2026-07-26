"""Parser registry and factory helpers."""

from __future__ import annotations

from typing import Dict

from parsers.axis_parser import AxisParser
from parsers.base_parser import BaseParser
from parsers.bob_parser import BobParser
from parsers.boi_parser import BoiParser
from parsers.canara_parser import CanaraParser
from parsers.deutsche_parser import DeutscheParser
from parsers.generic_parser import GenericParser
from parsers.hdfc_parser import HdfcParser
from parsers.icici_parser import IciciParser
from parsers.standard_chartered_parser import StandardCharteredParser
from parsers.svc_co_parser import SvcCoParser


def get_registry() -> Dict[str, BaseParser]:
    generic = GenericParser(key="generic", display_name="generic")
    canara = CanaraParser(key="canara", display_name="canara")
    icici = IciciParser(key="icici", display_name="icici")
    hdfc = HdfcParser(key="hdfc", display_name="hdfc")
    axis = AxisParser(key="axis", display_name="axis")
    bob = BobParser(key="bob", display_name="bob")
    boi = BoiParser(key="boi", display_name="boi")
    svc_co = SvcCoParser(key="svc_co", display_name="svc_co")
    standard_chartered = StandardCharteredParser(key="standard_chartered", display_name="standard_chartered")
    deutsche = DeutscheParser(key="deutsche", display_name="deutsche")
    return {
        generic.key: generic,
        canara.key: canara,
        icici.key: icici,
        hdfc.key: hdfc,
        axis.key: axis,
        bob.key: bob,
        boi.key: boi,
        svc_co.key: svc_co,
        standard_chartered.key: standard_chartered,
        deutsche.key: deutsche,
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

    if normalized == "hdfc":
        return HdfcParser(key="hdfc", display_name="hdfc")

    if normalized == "axis":
        return AxisParser(key="axis", display_name="axis")

    if normalized == "bob":
        return BobParser(key="bob", display_name="bob")

    if normalized == "boi":
        return BoiParser(key="boi", display_name="boi")

    if normalized == "svc_co":
        return SvcCoParser(key="svc_co", display_name="svc_co")

    if normalized == "standard_chartered":
        return StandardCharteredParser(key="standard_chartered", display_name="standard_chartered")

    if normalized == "deutsche":
        return DeutscheParser(key="deutsche", display_name="deutsche")

    return GenericParser(key=normalized, display_name=normalized)
