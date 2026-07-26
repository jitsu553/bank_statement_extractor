"""HDFC bank parser for statement format shown in provided sample.

HDFC internet-banking statements pack the Withdrawal and Deposit amounts
into two columns of identical shape (plain decimal numbers). pdfplumber's
plain-text extraction collapses both onto the same line with no delimiter,
so which column a given number came from cannot be recovered from text
alone; guessing from the running balance instead breaks whenever the
statement contains backdated/batched entries where the printed balance
doesn't move monotonically with each row (seen in this sample: a cluster of
FX-conversion/commission/GST fee rows all value-dated earlier than their
posting date). This parser therefore reads each page's *word positions*
(`PageExtraction.words`) instead of `page.text`, and assigns every word to
its real table column by table geometry:

    - Date / Value Dt are left-aligned date tokens; whichever of the two
      fixed x-positions (~34 / ~362) a token's left edge (x0) sits closer to
      decides the column.
    - Withdrawal / Deposit / Closing Balance are right-aligned amounts, so
      wider numbers push their left edge further left - only the right edge
      (x1) stays constant per column (~470 / ~548 / ~627), so amounts are
      matched to the column whose anchor their x1 is closest to.
    - Narration and Chq./Ref.No. are free text, split by a left-edge cutoff
      (~285) between the two columns.

These anchors were measured directly off this statement's header row and
data and are specific to this HDFC template.

HDFC also repeats a customer/account info block at the top of every page
(from "PageNo.:N Statementofaccount" down through "StatementFrom :") and a
boilerplate legal footer at the bottom of every page (from "HDFCBANKLIMITED"
down through "RegisteredOfficeAddress:"). Only page 1 carries the column
header row. A transaction's wrapped Narration can span a page boundary, with
the footer/next page's info block sitting in between the key row and its
continuation - so the whole document is treated as one continuous row
stream (not parsed page-by-page) rather than resetting state per page. A
final "STATEMENTSUMMARY" block near the end of the document is skipped the
same way as the per-page footer.

This module is intentionally self-contained (no shared helpers with
IciciParser/CanaraParser) so that changes here can never affect their
parsing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Pattern

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser

_DATE_WORD_RE = re.compile(r"^\d{2}/\d{2}/\d{2}$")
_AMOUNT_WORD_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")

# Column position anchors measured off this statement's header row/data
# (see module docstring for why x0 vs x1 is used per column).
_DATE_COL_X0 = 34.0
_VALUE_DT_COL_X0 = 362.0
_NARRATION_MAX_X0 = 285.0  # narration text never extends past ~278 in this template
_WITHDRAWAL_COL_X1 = 470.0
_DEPOSIT_COL_X1 = 548.0
_BALANCE_COL_X1 = 627.0

_ROW_TOP_TOLERANCE = 2.0  # points; groups words sharing a visual line

# Repeated per-page customer/account info block: starts at the page marker,
# ends at the StatementFrom line. Everything in between is address/account
# boilerplate that varies per customer, so it is skipped wholesale rather
# than pattern-matched line by line.
_INFO_BLOCK_START_RE = re.compile(r"^PageNo\.\s*:\s*\d+\s+Statementofaccount$", re.IGNORECASE)
_INFO_BLOCK_END_RE = re.compile(r"^StatementFrom\s*:", re.IGNORECASE)

# Repeated per-page legal footer, and the one-off STATEMENTSUMMARY block near
# the end of the document (both end at the RegisteredOfficeAddress line).
_FOOTER_BLOCK_START_RE = re.compile(r"^(HDFCBANKLIMITED|STATEMENTSUMMARY\b)", re.IGNORECASE)
_FOOTER_BLOCK_END_RE = re.compile(r"^RegisteredOfficeAddress\s*:", re.IGNORECASE)


class HdfcParser(BaseParser):
    key = "hdfc"
    display_name = "hdfc"

    def __init__(self, key: str = "hdfc", display_name: str = "hdfc") -> None:
        self.key = key
        self.display_name = display_name

    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        headers = [
            "Date",
            "Narration",
            "Chq./Ref.No.",
            "Value Dt",
            "Withdrawal Amt.",
            "Deposit Amt.",
            "Closing Balance",
        ]
        warnings: List[str] = []

        # Transactions and their wrapped continuations can cross page
        # boundaries (see module docstring), so every page's rows are
        # flattened into one continuous stream before assembling transactions.
        table_rows: List[List[Dict[str, Any]]] = []
        for page in pages:
            table_rows.extend(self._group_words_into_rows(page.words))

        rows = self._assemble_transactions(table_rows)

        if not rows:
            warnings.append("No HDFC transaction rows were parsed from text content.")

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
        last_row: Optional[Dict[str, str]] = None
        skip_until: Optional[Pattern[str]] = None

        for words in table_rows:
            row_text = " ".join(str(word["text"]) for word in words).strip()
            if not row_text:
                continue

            if skip_until is not None:
                if skip_until.match(row_text):
                    skip_until = None
                continue

            if _INFO_BLOCK_START_RE.match(row_text):
                skip_until = _INFO_BLOCK_END_RE
                continue

            if _FOOTER_BLOCK_START_RE.match(row_text):
                skip_until = _FOOTER_BLOCK_END_RE
                continue

            if self._is_header_line(row_text):
                continue

            columns = self._classify_row(words)

            if columns["date"]:
                row = {
                    "Date": columns["date"],
                    "Narration": columns["narration"],
                    "Chq./Ref.No.": columns["refno"],
                    "Value Dt": columns["value_dt"],
                    "Withdrawal Amt.": columns["withdrawal"],
                    "Deposit Amt.": columns["deposit"],
                    "Closing Balance": columns["balance"],
                }
                rows.append(row)
                last_row = row
                continue

            # Not a key row: a wrapped continuation of the current
            # transaction's Narration. HDFC always starts a new transaction's
            # narration on the same line as its date, so any other row here
            # belongs to the previous transaction.
            if last_row is not None and columns["narration"]:
                last_row["Narration"] = (last_row["Narration"] + " " + columns["narration"]).strip()

        return rows

    @staticmethod
    def _classify_row(words: List[Dict[str, Any]]) -> Dict[str, str]:
        date = ""
        value_dt = ""
        refno_parts: List[str] = []
        narration_parts: List[str] = []
        withdrawal = ""
        deposit = ""
        balance = ""

        for word in sorted(words, key=lambda w: w["x0"]):
            text = str(word["text"])
            x0 = float(word["x0"])
            x1 = float(word["x1"])

            if _DATE_WORD_RE.match(text):
                if abs(x0 - _DATE_COL_X0) <= abs(x0 - _VALUE_DT_COL_X0):
                    date = text
                else:
                    value_dt = text
                continue

            if _AMOUNT_WORD_RE.match(text):
                distances = {
                    "withdrawal": abs(x1 - _WITHDRAWAL_COL_X1),
                    "deposit": abs(x1 - _DEPOSIT_COL_X1),
                    "balance": abs(x1 - _BALANCE_COL_X1),
                }
                nearest = min(distances, key=lambda k: distances[k])
                if nearest == "withdrawal":
                    withdrawal = text
                elif nearest == "deposit":
                    deposit = text
                else:
                    balance = text
                continue

            if x0 < _NARRATION_MAX_X0:
                narration_parts.append(text)
            else:
                refno_parts.append(text)

        return {
            "date": date,
            "value_dt": value_dt,
            "refno": " ".join(refno_parts),
            "narration": " ".join(narration_parts),
            "withdrawal": withdrawal,
            "deposit": deposit,
            "balance": balance,
        }

    @staticmethod
    def _is_header_line(line: str) -> bool:
        normalized = re.sub(r"[^a-z\s]", " ", line.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        marker_tokens = ["date", "narration", "withdrawal", "deposit", "closing", "balance"]
        return all(token in normalized for token in marker_tokens)
