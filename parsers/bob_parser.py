"""Bank of Baroda (BOB) parser for statement format shown in provided sample.

Like HDFC and Axis, BOB packs Withdrawal(DR) and Deposit(CR) into two
columns of identical shape (plain decimal numbers) that pdfplumber's plain
text extraction would collapse together with no delimiter - so which
column a printed number came from cannot be recovered from text alone.
Rather than guess from the running balance (fragile - see HdfcParser's
docstring for why that approach broke on a real statement), this parser
reads each page's word positions (`PageExtraction.words`) and assigns every
word to its actual table column by geometry:

    - Tran Date / Value Date are left-aligned date tokens; whichever of the
      two fixed x-positions (~15 / ~87) a token's left edge (x0) sits closer
      to decides the column.
    - Narration / Chq.No. are free text, split by a left-edge cutoff (~362)
      between the two columns (Chq.No. is never actually populated in this
      sample, but the column is kept so the output matches the PDF).
    - Withdrawal(DR) / Deposit(CR) are right-aligned amounts, so wider
      numbers push their left edge further left - only the right edge (x1)
      stays constant per column (~543 / ~662), so a plain amount is matched
      to whichever anchor its x1 is closest to.
    - Balance is unambiguous without needing position at all: BOB appends a
      literal "Cr"/"Dr" suffix directly onto the balance figure (e.g.
      "3,24,633.95Cr"), which withdrawal/deposit amounts never carry.

These anchors were measured directly off this statement's header row and
data and are specific to this BOB template.

Each transaction's key row (Tran Date + Value Date + first Narration
fragment + Balance) is sometimes followed by one or more continuation rows
before the next key row - unlike Axis (which wraps Narration only *before*
the key row), BOB wraps only *after* it, and the Withdrawal/Deposit amount
itself is frequently pushed onto one of those continuation rows rather than
appearing on the key row (long narrations push the amount down; short ones
keep it inline). So continuation rows are scanned for whichever of
Narration text / Chq.No. / amount they carry and merged into the
transaction most recently opened by a key row.

BOB does not repeat the customer/account info block on every page (unlike
HDFC), but each page ends with a two-line footer ("<date> <time>
Contact-Us@18005700 Page N of 14" / "*This is computer-generated
statement...") that must be skipped explicitly - the footer's own leading
date would otherwise be misread as a stray Value Date. The whole document
is processed as one continuous row stream (not per page) so a transaction
wrapped across a page boundary is not split, and so the header row (which,
like HDFC, only appears once on page 1) gates out the leading letterhead
via a single `in_table` flag.

This module is intentionally self-contained (no shared helpers with
HdfcParser/AxisParser/IciciParser/CanaraParser) so that changes here can
never affect their parsing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser

_DATE_WORD_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_BALANCE_WORD_RE = re.compile(r"^-?\d[\d,]*\.\d{2}(Cr|Dr)$", re.IGNORECASE)
_AMOUNT_WORD_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")

# Column position anchors measured off this statement's header row/data
# (see module docstring for why x0 vs x1 is used per column).
_TRAN_DATE_COL_X0 = 15.0
_VALUE_DATE_COL_X0 = 87.0
_CHQNO_MIN_X0 = 362.0  # narration text never extends past ~360 in this template
_WITHDRAWAL_COL_X1 = 543.0
_DEPOSIT_COL_X1 = 662.0

_ROW_TOP_TOLERANCE = 2.0  # points; groups words sharing a visual line

# Per-page footer: "<date> <time> Contact-Us@18005700 Page N of 14" followed
# by a disclaimer line. Its own leading date would otherwise be misread as a
# stray transaction date, so it is matched and skipped by content.
_FOOTER_LINE_RE = re.compile(r"CONTACT-US@18005700|^\*THIS IS COMPUTER-GENERATED", re.IGNORECASE)


class BobParser(BaseParser):
    key = "bob"
    display_name = "bob"

    def __init__(self, key: str = "bob", display_name: str = "bob") -> None:
        self.key = key
        self.display_name = display_name

    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        headers = ["Tran Date", "Value Date", "Narration", "Chq No", "Withdrawal(Dr)", "Deposit(Cr)", "Balance"]
        warnings: List[str] = []

        # A transaction's continuation rows can follow its key row across a
        # page boundary, so every page's rows are flattened into one
        # continuous stream before assembling transactions.
        table_rows: List[List[Dict[str, Any]]] = []
        for page in pages:
            table_rows.extend(self._group_words_into_rows(page.words))

        rows = self._assemble_transactions(table_rows)

        if not rows:
            warnings.append("No BOB transaction rows were parsed from text content.")

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

            if _FOOTER_LINE_RE.search(row_text):
                continue

            columns = self._classify_row(words)

            if columns["tran_date"]:
                row = {
                    "Tran Date": columns["tran_date"],
                    "Value Date": columns["value_date"],
                    "Narration": columns["narration"],
                    "Chq No": columns["chqno"],
                    "Withdrawal(Dr)": columns["withdrawal"],
                    "Deposit(Cr)": columns["deposit"],
                    "Balance": columns["balance"],
                }
                rows.append(row)
                last_row = row
                continue

            # Not a key row: a continuation of the current transaction,
            # carrying more Narration text, a Chq.No., and/or the
            # Withdrawal/Deposit amount when it didn't fit on the key row.
            if last_row is None:
                continue

            if columns["narration"]:
                last_row["Narration"] = (last_row["Narration"] + " " + columns["narration"]).strip()
            if columns["chqno"]:
                last_row["Chq No"] = (last_row["Chq No"] + " " + columns["chqno"]).strip()
            if columns["withdrawal"] and not last_row["Withdrawal(Dr)"]:
                last_row["Withdrawal(Dr)"] = columns["withdrawal"]
            if columns["deposit"] and not last_row["Deposit(Cr)"]:
                last_row["Deposit(Cr)"] = columns["deposit"]
            if columns["balance"] and not last_row["Balance"]:
                last_row["Balance"] = columns["balance"]

        return rows

    @staticmethod
    def _classify_row(words: List[Dict[str, Any]]) -> Dict[str, str]:
        tran_date = ""
        value_date = ""
        narration_parts: List[str] = []
        chqno_parts: List[str] = []
        withdrawal = ""
        deposit = ""
        balance = ""

        for word in sorted(words, key=lambda w: w["x0"]):
            text = str(word["text"])
            x0 = float(word["x0"])
            x1 = float(word["x1"])

            if _BALANCE_WORD_RE.match(text):
                balance = text
                continue

            if _DATE_WORD_RE.match(text):
                if abs(x0 - _TRAN_DATE_COL_X0) <= abs(x0 - _VALUE_DATE_COL_X0):
                    tran_date = text
                else:
                    value_date = text
                continue

            if _AMOUNT_WORD_RE.match(text):
                if abs(x1 - _WITHDRAWAL_COL_X1) <= abs(x1 - _DEPOSIT_COL_X1):
                    withdrawal = text
                else:
                    deposit = text
                continue

            if x0 >= _CHQNO_MIN_X0:
                chqno_parts.append(text)
            else:
                narration_parts.append(text)

        return {
            "tran_date": tran_date,
            "value_date": value_date,
            "narration": " ".join(narration_parts),
            "chqno": " ".join(chqno_parts),
            "withdrawal": withdrawal,
            "deposit": deposit,
            "balance": balance,
        }

    @staticmethod
    def _is_header_line(line: str) -> bool:
        normalized = re.sub(r"[^a-z\s]", " ", line.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        marker_tokens = ["tran", "date", "narration", "withdrawal", "deposit", "balance"]
        return all(token in normalized for token in marker_tokens)
