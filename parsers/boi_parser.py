"""Bank of India (BOI) parser for statement format shown in provided sample.

Like HDFC/Axis/BOB, BOI packs Debit and Credit into two columns of
identical shape (plain decimal numbers) that pdfplumber's plain text
extraction would collapse together with no delimiter - so which column a
printed number came from cannot be recovered from text alone. Rather than
guess from the running balance (fragile - see HdfcParser's docstring for
why that approach broke on a real statement), this parser reads each
page's word positions (`PageExtraction.words`) and assigns every word to
its actual table column by geometry:

    - Sr No / Date are left-aligned tokens at fixed x-positions (~132 /
      ~234); their own distinct shapes (plain integer vs DD-MM-YYYY) mean no
      anchor comparison is even needed to tell them apart, only a loose
      bound on Sr No's position to guard against a stray bare-digit token
      elsewhere in the row.
    - Remarks is free text starting at a fixed left edge (~371).
    - Debit / Credit / Balance are right-aligned amounts, so wider numbers
      push their left edge further left - only the right edge (x1) stays
      constant per column (~987 / ~1180 / ~1408), so a plain amount is
      matched to whichever anchor its x1 is closest to. The literal "Rs"
      (Rupee) symbol printed just before the balance figure is discarded;
      it is not needed to identify the Balance column since the x1 anchor
      already does that unambiguously.

These anchors were measured directly off this statement's header row and
data and are specific to this BOI template.

Each transaction's key row (Sr No + Date + first Remarks fragment + Debit
or Credit amount) is followed by one or more continuation rows before the
next key row - the Balance always lands on its own continuation row (paired
with the Rupee symbol), and Remarks text may continue on rows before *and*
after that Balance row. So, like BobParser, continuation rows are scanned
for whichever of Remarks text / Balance they carry and merged into the
transaction most recently opened by a key row.

BOI does not repeat a customer/account info block on every page (unlike
HDFC); the whole document is processed as one continuous row stream (not
per page) so a transaction wrapped across a page boundary is not split, and
so a single `in_table` flag gates out the leading letterhead that (like
HDFC/BOB) only appears once on page 1, before the "Sr No Date Remarks ..."
header row. A trailing "NOTE:" disclaimer block after the last transaction
on the final page is recognised explicitly and stops continuation-merging,
so it is not swept into the last transaction's Remarks.

This module is intentionally self-contained (no shared helpers with
HdfcParser/AxisParser/BobParser/IciciParser/CanaraParser) so that changes
here can never affect their parsing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser

_SR_NO_WORD_RE = re.compile(r"^\d{1,4}$")
_DATE_WORD_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
_AMOUNT_WORD_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")
_RUPEE_SYMBOL_RE = re.compile(r"^₹$")  # the "Rs" glyph printed before Balance

# Column position anchors measured off this statement's header row/data
# (see module docstring for why x0 vs x1 is used per column).
_SR_NO_MAX_X0 = 200.0
_REMARKS_MIN_X0 = 360.0  # Remarks text starts ~371 in this template
_DEBIT_COL_X1 = 987.0
_CREDIT_COL_X1 = 1180.0
_BALANCE_COL_X1 = 1408.0

_ROW_TOP_TOLERANCE = 2.0  # points; groups words sharing a visual line

_NOTE_BLOCK_RE = re.compile(r"^NOTE\s*:", re.IGNORECASE)


class BoiParser(BaseParser):
    key = "boi"
    display_name = "boi"

    def __init__(self, key: str = "boi", display_name: str = "boi") -> None:
        self.key = key
        self.display_name = display_name

    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        headers = ["Sr No", "Date", "Remarks", "Debit", "Credit", "Balance"]
        warnings: List[str] = []

        # A transaction's continuation rows can follow its key row across a
        # page boundary, so every page's rows are flattened into one
        # continuous stream before assembling transactions.
        table_rows: List[List[Dict[str, Any]]] = []
        for page in pages:
            table_rows.extend(self._group_words_into_rows(page.words))

        rows = self._assemble_transactions(table_rows)

        if not rows:
            warnings.append("No BOI transaction rows were parsed from text content.")

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
        last_row: Optional[Dict[str, str]] = None

        for words in table_rows:
            row_text = " ".join(str(word["text"]) for word in sorted(words, key=lambda w: w["x0"])).strip()
            if not row_text:
                continue

            if not in_table:
                if self._is_header_line(row_text):
                    in_table = True
                continue

            if _NOTE_BLOCK_RE.match(row_text):
                last_row = None
                continue

            columns = self._classify_row(words)

            if columns["date"]:
                row = {
                    "Sr No": columns["sr_no"],
                    "Date": columns["date"],
                    "Remarks": columns["remarks"],
                    "Debit": columns["debit"],
                    "Credit": columns["credit"],
                    "Balance": columns["balance"],
                }
                rows.append(row)
                last_row = row
                continue

            # Not a key row: a continuation of the current transaction,
            # carrying more Remarks text and/or the Balance (which always
            # lands on its own row here, alongside the Rupee symbol).
            if last_row is None:
                continue

            if columns["remarks"]:
                last_row["Remarks"] = (last_row["Remarks"] + " " + columns["remarks"]).strip()
            if columns["debit"] and not last_row["Debit"]:
                last_row["Debit"] = columns["debit"]
            if columns["credit"] and not last_row["Credit"]:
                last_row["Credit"] = columns["credit"]
            if columns["balance"] and not last_row["Balance"]:
                last_row["Balance"] = columns["balance"]

        return rows

    @staticmethod
    def _classify_row(words: List[Dict[str, Any]]) -> Dict[str, str]:
        sr_no = ""
        date = ""
        remarks_parts: List[str] = []
        debit = ""
        credit = ""
        balance = ""

        for word in sorted(words, key=lambda w: w["x0"]):
            text = str(word["text"])
            x0 = float(word["x0"])
            x1 = float(word["x1"])

            if _RUPEE_SYMBOL_RE.match(text):
                continue

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

            if _SR_NO_WORD_RE.match(text) and x0 < _SR_NO_MAX_X0:
                sr_no = text
                continue

            if x0 >= _REMARKS_MIN_X0:
                remarks_parts.append(text)

        return {
            "sr_no": sr_no,
            "date": date,
            "remarks": " ".join(remarks_parts),
            "debit": debit,
            "credit": credit,
            "balance": balance,
        }

    @staticmethod
    def _is_header_line(line: str) -> bool:
        normalized = re.sub(r"[^a-z\s]", " ", line.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        marker_tokens = ["sr", "no", "date", "remarks", "debit", "credit", "balance"]
        return all(token in normalized for token in marker_tokens)
