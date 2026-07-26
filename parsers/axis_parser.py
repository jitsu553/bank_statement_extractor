"""Axis bank parser for statement format shown in provided sample.

Like HDFC, Axis packs Debit and Credit into two columns of identical shape
(plain decimal numbers) that pdfplumber's plain-text extraction would
collapse onto one line with no delimiter between them - so which column a
printed number came from cannot be recovered from text alone. Rather than
guess from the running balance (fragile - see HdfcParser's docstring for why
that approach broke on real statements), this parser reads each page's word
positions (`PageExtraction.words`) and assigns every word to its actual
table column by geometry:

    - Tran Date / Chq No / Particulars / Init. Br are left-aligned, so a
      word's left edge (x0) identifies its column.
    - Debit / Credit / Balance are right-aligned amounts, so wider numbers
      push their left edge further left - only the right edge (x1) stays
      constant per column, so amounts are matched to the column whose
      anchor their x1 is closest to (Debit ~379, Credit ~442, Balance ~531).

These anchors were measured directly off this statement's header row and
data and are specific to this Axis template.

Unlike ICICI, Axis's Particulars text wraps only *before* the key line (the
line carrying the date and amounts) - never after - so continuation lines
are simply buffered until the next date-bearing row is seen, then attached
as the leading part of that transaction's Particulars. Unlike HDFC, Axis
does not repeat a customer/account info block on every page, so no
per-page skip-blocks are needed; the whole document is still processed as
one continuous row stream (rather than per page) purely so a transaction
wrapped across a page boundary is not split, and to let a single `in_table`
flag gate out the letterhead that precedes the "Tran Date ..." header row
which (unlike later pages) only appears once, on page 1.

The OPENING BALANCE / CLOSING BALANCE / TRANSACTION TOTAL summary lines
carry no date, so they are recognised explicitly and emitted as their own
rows rather than being swept into the next transaction's buffered
Particulars.

This module is intentionally self-contained (no shared helpers with
HdfcParser/IciciParser/CanaraParser) so that changes here can never affect
their parsing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser

_DATE_WORD_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
_AMOUNT_WORD_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")

# Column position anchors measured off this statement's header row/data
# (see module docstring for why x0 vs x1 is used per column).
_CHQNO_MIN_X0 = 85.0
_PARTICULARS_MIN_X0 = 112.0
_INIT_BR_MIN_X0 = 535.0
_DEBIT_COL_X1 = 379.0
_CREDIT_COL_X1 = 442.0
_BALANCE_COL_X1 = 531.0

_ROW_TOP_TOLERANCE = 2.0  # points; groups words sharing a visual line

_OPENING_BALANCE_RE = re.compile(r"^OPENING\s+BALANCE\b", re.IGNORECASE)
_CLOSING_BALANCE_RE = re.compile(r"^CLOSING\s+BALANCE\b", re.IGNORECASE)
_TRANSACTION_TOTAL_RE = re.compile(r"^TRANSACTION\s+TOTAL\b", re.IGNORECASE)


class AxisParser(BaseParser):
    key = "axis"
    display_name = "axis"

    def __init__(self, key: str = "axis", display_name: str = "axis") -> None:
        self.key = key
        self.display_name = display_name

    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        headers = ["Tran Date", "Chq No", "Particulars", "Debit", "Credit", "Balance", "Init. Br"]
        warnings: List[str] = []

        # A transaction's buffered (wrapped) Particulars lines can precede
        # its key line across a page boundary, so every page's rows are
        # flattened into one continuous stream before assembling transactions.
        table_rows: List[List[Dict[str, Any]]] = []
        for page in pages:
            table_rows.extend(self._group_words_into_rows(page.words))

        rows = self._assemble_transactions(table_rows)

        if not rows:
            warnings.append("No Axis transaction rows were parsed from text content.")

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

        for words in table_rows:
            row_text = " ".join(str(word["text"]) for word in sorted(words, key=lambda w: w["x0"])).strip()
            if not row_text:
                continue

            if not in_table:
                if self._is_header_line(row_text):
                    in_table = True
                continue

            columns = self._classify_row(words)

            if (
                _OPENING_BALANCE_RE.match(row_text)
                or _CLOSING_BALANCE_RE.match(row_text)
                or _TRANSACTION_TOTAL_RE.match(row_text)
            ):
                if _OPENING_BALANCE_RE.match(row_text):
                    label = "Opening Balance"
                elif _CLOSING_BALANCE_RE.match(row_text):
                    label = "Closing Balance"
                else:
                    label = "Transaction Total"
                rows.append(
                    {
                        "Tran Date": "",
                        "Chq No": "",
                        "Particulars": label,
                        "Debit": columns["debit"],
                        "Credit": columns["credit"],
                        "Balance": columns["balance"],
                        "Init. Br": "",
                    }
                )
                continue

            if columns["date"]:
                particulars = " ".join(particulars_buffer + ([columns["particulars"]] if columns["particulars"] else [])).strip()
                particulars_buffer = []

                rows.append(
                    {
                        "Tran Date": columns["date"],
                        "Chq No": columns["chqno"],
                        "Particulars": particulars,
                        "Debit": columns["debit"],
                        "Credit": columns["credit"],
                        "Balance": columns["balance"],
                        "Init. Br": columns["initbr"],
                    }
                )
                continue

            # Not a key row: Axis wraps Particulars text only *before* the
            # key line, so this is buffered as the leading part of the next
            # transaction's Particulars.
            if columns["particulars"]:
                particulars_buffer.append(columns["particulars"])

        return rows

    @staticmethod
    def _classify_row(words: List[Dict[str, Any]]) -> Dict[str, str]:
        date = ""
        chqno_parts: List[str] = []
        particulars_parts: List[str] = []
        initbr_parts: List[str] = []
        debit = ""
        credit = ""
        balance = ""

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
                    balance = text
                continue

            if x0 >= _INIT_BR_MIN_X0:
                initbr_parts.append(text)
            elif x0 >= _PARTICULARS_MIN_X0:
                particulars_parts.append(text)
            elif x0 >= _CHQNO_MIN_X0:
                chqno_parts.append(text)
            else:
                # Left of the Chq No column (shouldn't normally happen inside
                # the table other than the Date column, already handled above).
                particulars_parts.append(text)

        return {
            "date": date,
            "chqno": " ".join(chqno_parts),
            "particulars": " ".join(particulars_parts),
            "debit": debit,
            "credit": credit,
            "balance": balance,
            "initbr": " ".join(initbr_parts),
        }

    @staticmethod
    def _is_header_line(line: str) -> bool:
        normalized = re.sub(r"[^a-z\s]", " ", line.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        marker_tokens = ["tran", "date", "particulars", "debit", "credit", "balance"]
        return all(token in normalized for token in marker_tokens)
