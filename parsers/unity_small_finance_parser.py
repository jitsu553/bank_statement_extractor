"""Unity Small Finance Bank parser for statement format shown in provided sample.

Unlike the other banks handled so far, Unity always prints *both* Debit and
Credit on every transaction row - whichever one does not apply is shown as
a plain "0.00" rather than being left blank - so there is no column-collapse
ambiguity to resolve here. Word positions (`PageExtraction.words`) are still
used, though, both for robustness (matching each amount to Debit/Credit/
Balance by its right edge x1, since wider numbers push their left edge
further left) and because Particulars text wraps *before* the key line
(the line carrying the date and amounts), the same layout as AxisParser:
continuation lines are buffered and attached to the *next* key row rather
than the previous one.

Column anchors (x0 for the left-aligned Date/Particulars/Chq.Ref columns,
x1 for the right-aligned Debit/Credit/Closing Balance columns) were measured
directly off this statement's header row and data and are specific to this
Unity template. The Closing Balance column carries a standalone "Cr"/"Dr"
suffix word right after the figure, which is recombined into one string to
match how the PDF displays it.

An "Opening Balance" row (with no date, and in this sample no amount at
all - just "Cr") and a "Closing Balance" row (split across two visual
lines close enough together to need a slightly larger row-clustering
tolerance than the other parsers use) both carry no date and are
recognised explicitly rather than being swept into a transaction's
Particulars. A trailing "STATEMENT SUMMARY" block and disclaimer footer
carry no date either, so - same as AxisParser - they are simply never
flushed into any row once the last real key row has been emitted.

This module is intentionally self-contained (no shared helpers with
AxisParser/HdfcParser/BobParser/BoiParser/SvcCoParser/
StandardCharteredParser/DeutscheParser/IciciParser/CanaraParser) so that
changes here can never affect their parsing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser

_DATE_WORD_RE = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{4}$")
_AMOUNT_WORD_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")
_BALANCE_SUFFIX_RE = re.compile(r"^(CR|DR)$", re.IGNORECASE)

# Column position anchors measured off this statement's header row/data
# (see module docstring for why x0 vs x1 is used per column).
_CHQREF_MIN_X0 = 240.0  # Particulars text never reaches past ~234 in this template
_DEBIT_COL_X1 = 377.0
_CREDIT_COL_X1 = 459.0
_BALANCE_COL_X1 = 551.3
_BALANCE_SUFFIX_MIN_X0 = 545.0

# The "Closing Balance" label and its amount render on two visual lines only
# ~4pt apart (tighter than the normal ~10pt+ gap between distinct rows in
# this template), so they need a slightly larger clustering tolerance to be
# treated as one row.
_ROW_TOP_TOLERANCE = 5.0

_OPENING_BALANCE_RE = re.compile(r"^OPENING\s+BALANCE\b", re.IGNORECASE)
_CLOSING_BALANCE_RE = re.compile(r"^CLOSING\s+BALANCE\b", re.IGNORECASE)


class UnitySmallFinanceParser(BaseParser):
    key = "unity_small_finance"
    display_name = "unity_small_finance"

    def __init__(self, key: str = "unity_small_finance", display_name: str = "unity_small_finance") -> None:
        self.key = key
        self.display_name = display_name

    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        headers = ["Date", "Particulars", "Chq./Ref. Number", "Debit", "Credit", "Closing Balance"]
        warnings: List[str] = []

        # A transaction's buffered (wrapped) Particulars lines can precede
        # its key line across a page boundary, so every page's rows are
        # flattened into one continuous stream before assembling transactions.
        table_rows: List[List[Dict[str, Any]]] = []
        for page in pages:
            table_rows.extend(self._group_words_into_rows(page.words))

        rows = self._assemble_transactions(table_rows)

        if not rows:
            warnings.append("No Unity Small Finance Bank transaction rows were parsed from text content.")

        return ParseResult(headers=headers, rows=rows, warnings=warnings, parser_name=self.display_name)

    @staticmethod
    def _group_words_into_rows(words: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        """Cluster words sharing a visual line, in reading order."""
        rows: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        current_top: Optional[float] = None

        for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
            top = float(word["top"])
            if current_top is not None and abs(top - current_top) > _ROW_TOP_TOLERANCE:
                rows.append(current)
                current = []
                current_top = None
            current.append(word)
            if current_top is None:
                current_top = top

        if current:
            rows.append(current)

        return rows

    def _assemble_transactions(self, table_rows: List[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        in_table = False
        particulars_buffer: List[str] = []
        chqref_buffer: List[str] = []

        for words in table_rows:
            row_text = " ".join(str(word["text"]) for word in sorted(words, key=lambda w: w["x0"])).strip()
            if not row_text:
                continue

            if not in_table:
                if self._is_header_line(row_text):
                    in_table = True
                continue

            columns = self._classify_row(words)

            if _OPENING_BALANCE_RE.match(row_text) or _CLOSING_BALANCE_RE.match(row_text):
                label = "Opening Balance" if _OPENING_BALANCE_RE.match(row_text) else "Closing Balance"
                rows.append(
                    {
                        "Date": "",
                        "Particulars": label,
                        "Chq./Ref. Number": "",
                        "Debit": "",
                        "Credit": "",
                        "Closing Balance": columns["balance"],
                    }
                )
                particulars_buffer = []
                chqref_buffer = []
                continue

            if columns["date"]:
                particulars = " ".join(
                    particulars_buffer + ([columns["particulars"]] if columns["particulars"] else [])
                ).strip()
                chqref = " ".join(chqref_buffer + ([columns["chqref"]] if columns["chqref"] else [])).strip()
                particulars_buffer = []
                chqref_buffer = []

                rows.append(
                    {
                        "Date": columns["date"],
                        "Particulars": particulars,
                        "Chq./Ref. Number": chqref,
                        "Debit": columns["debit"],
                        "Credit": columns["credit"],
                        "Closing Balance": columns["balance"],
                    }
                )
                continue

            # Not a key row: Unity wraps Particulars text only *before* the
            # key line, so this is buffered as the leading part of the next
            # transaction's Particulars.
            if columns["particulars"]:
                particulars_buffer.append(columns["particulars"])
            if columns["chqref"]:
                chqref_buffer.append(columns["chqref"])

        return rows

    @staticmethod
    def _classify_row(words: List[Dict[str, Any]]) -> Dict[str, str]:
        date = ""
        particulars_parts: List[str] = []
        chqref_parts: List[str] = []
        debit = ""
        credit = ""
        balance_amount = ""
        balance_suffix = ""

        for word in sorted(words, key=lambda w: w["x0"]):
            text = str(word["text"])
            x0 = float(word["x0"])
            x1 = float(word["x1"])

            if _DATE_WORD_RE.match(text):
                date = text
                continue

            if _AMOUNT_WORD_RE.match(text):
                distances = {
                    "debit": abs(x1 - _DEBIT_COL_X1),
                    "credit": abs(x1 - _CREDIT_COL_X1),
                    "balance": abs(x1 - _BALANCE_COL_X1),
                }
                nearest = min(distances, key=lambda k: distances[k])
                if nearest == "debit":
                    debit = text
                elif nearest == "credit":
                    credit = text
                else:
                    balance_amount = text
                continue

            if _BALANCE_SUFFIX_RE.match(text) and x0 >= _BALANCE_SUFFIX_MIN_X0:
                balance_suffix = text.upper()
                continue

            if x0 >= _CHQREF_MIN_X0:
                chqref_parts.append(text)
            else:
                particulars_parts.append(text)

        balance = " ".join(part for part in (balance_amount, balance_suffix) if part)

        return {
            "date": date,
            "particulars": " ".join(particulars_parts),
            "chqref": " ".join(chqref_parts),
            "debit": debit,
            "credit": credit,
            "balance": balance,
        }

    @staticmethod
    def _is_header_line(line: str) -> bool:
        normalized = re.sub(r"[^a-z\s]", " ", line.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        marker_tokens = ["date", "particulars", "debit", "credit", "closing", "balance"]
        return all(token in normalized for token in marker_tokens)
