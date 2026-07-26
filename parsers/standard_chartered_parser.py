"""Standard Chartered parser for statement format shown in provided sample.

Like the other single-amount-column banks handled so far, Standard
Chartered packs Deposit and Withdrawal into two columns of identical shape
(plain decimal numbers) that pdfplumber's plain text extraction would
collapse together with no delimiter - so which column a printed number
came from cannot be recovered from text alone. Rather than guess from the
running balance (fragile - see HdfcParser's docstring for why that
approach broke on a real statement), this parser reads each page's word
positions (`PageExtraction.words`) and assigns every word to its actual
table column by geometry:

    - Date / Value Date are each rendered as TWO separate words (a month
      abbreviation and a day number, e.g. "Apr" "02"), not one token, so
      they are recombined from whichever left-aligned x0 band they fall in
      (~30-70 for Date, ~70-115 for Value Date).
    - Description / Cheque are free text, split by a left-edge cutoff
      (~330) between the two columns.
    - Deposit / Withdrawal / Balance are right-aligned amounts, so wider
      numbers push their left edge further left - only the right edge (x1)
      stays constant per column (~431 / ~503 / ~563), so a plain amount is
      matched to whichever anchor its x1 is closest to.

These anchors were measured directly off this statement's header row and
data and are specific to this Standard Chartered template.

A quirk specific to this bank: the Date column is left blank (only Value
Date is printed) for a transaction that shares the same Date as the one
immediately above it - visually "ditto". This parser fills that blank in
with the last Date actually printed, since that is the transaction's real
date, not a missing value; a row only counts as a transaction ("key row")
at all when it has a Value Date, since that column is never blank.

Standard Chartered repeats the full customer/account info block *and* the
"Date Value Description Cheque Deposit Withdrawal Balance" header on every
single page (unlike HDFC, which only prints the header once), each
followed immediately by a "Balance Brought Forward <amount>" row carrying
that page's opening balance. Each page also ends with a "Page N of 71" /
print-date / disclaimer footer. Because the header recurs, a single
`skipping` flag (rather than a one-shot `in_table` flag) is toggled back on
by the footer and only released again once the next header row is seen,
so every repeat of the info block and footer is skipped uniformly. The
whole document is still processed as one continuous row stream (not per
page) so a transaction wrapped across a page boundary is not split.

This module is intentionally self-contained (no shared helpers with
HdfcParser/AxisParser/BobParser/BoiParser/SvcCoParser/IciciParser/
CanaraParser) so that changes here can never affect their parsing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser

_MONTH_RE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$", re.IGNORECASE)
_DAY_RE = re.compile(r"^\d{1,2}$")
_AMOUNT_WORD_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")

# Column position anchors measured off this statement's header row/data
# (see module docstring for why x0 vs x1 is used per column).
_TRAN_DATE_MAX_X0 = 70.0
_VALUE_DATE_MAX_X0 = 115.0
_CHEQUE_MIN_X0 = 330.0
_DEPOSIT_COL_X1 = 431.0
_WITHDRAWAL_COL_X1 = 503.0
_BALANCE_COL_X1 = 563.0

_ROW_TOP_TOLERANCE = 2.0  # points; groups words sharing a visual line

_FOOTER_START_RE = re.compile(r"^Page\s+\d+\s+of\s+\d+", re.IGNORECASE)
_BALANCE_BF_RE = re.compile(r"^Balance\s+Brought\s+Forward\b", re.IGNORECASE)
_TOTAL_RE = re.compile(r"^Total\b", re.IGNORECASE)


class StandardCharteredParser(BaseParser):
    key = "standard_chartered"
    display_name = "standard_chartered"

    def __init__(self, key: str = "standard_chartered", display_name: str = "standard_chartered") -> None:
        self.key = key
        self.display_name = display_name

    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        headers = ["Date", "Value Date", "Description", "Cheque", "Deposit", "Withdrawal", "Balance"]
        warnings: List[str] = []

        # A transaction's continuation rows can follow its key row across a
        # page boundary, so every page's rows are flattened into one
        # continuous stream before assembling transactions.
        table_rows: List[List[Dict[str, Any]]] = []
        for page in pages:
            table_rows.extend(self._group_words_into_rows(page.words))

        rows = self._assemble_transactions(table_rows)

        if not rows:
            warnings.append("No Standard Chartered transaction rows were parsed from text content.")

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
        skipping = True  # the document opens with the page-1 info block, before any header
        last_row: Optional[Dict[str, str]] = None
        current_tran_date = ""

        for words in table_rows:
            row_text = " ".join(str(word["text"]) for word in sorted(words, key=lambda w: w["x0"])).strip()
            if not row_text:
                continue

            if skipping:
                if self._is_header_line(row_text):
                    skipping = False
                continue

            if _FOOTER_START_RE.match(row_text):
                skipping = True
                continue

            columns = self._classify_row(words)

            if _BALANCE_BF_RE.match(row_text):
                rows.append(
                    {
                        "Date": "",
                        "Value Date": "",
                        "Description": "Balance Brought Forward",
                        "Cheque": "",
                        "Deposit": "",
                        "Withdrawal": "",
                        "Balance": columns["balance"],
                    }
                )
                last_row = None
                continue

            if _TOTAL_RE.match(row_text) and not columns["value_date"]:
                rows.append(
                    {
                        "Date": "",
                        "Value Date": "",
                        "Description": "Total",
                        "Cheque": "",
                        "Deposit": columns["deposit"],
                        "Withdrawal": columns["withdrawal"],
                        "Balance": "",
                    }
                )
                last_row = None
                continue

            if columns["value_date"]:
                if columns["tran_date"]:
                    current_tran_date = columns["tran_date"]
                row = {
                    "Date": current_tran_date,
                    "Value Date": columns["value_date"],
                    "Description": columns["description"],
                    "Cheque": columns["cheque"],
                    "Deposit": columns["deposit"],
                    "Withdrawal": columns["withdrawal"],
                    "Balance": columns["balance"],
                }
                rows.append(row)
                last_row = row
                continue

            # Not a key row: a wrapped continuation of the current
            # transaction's Description.
            if last_row is None:
                continue

            if columns["description"]:
                last_row["Description"] = (last_row["Description"] + " " + columns["description"]).strip()
            if columns["cheque"]:
                last_row["Cheque"] = (last_row["Cheque"] + " " + columns["cheque"]).strip()
            if columns["deposit"] and not last_row["Deposit"]:
                last_row["Deposit"] = columns["deposit"]
            if columns["withdrawal"] and not last_row["Withdrawal"]:
                last_row["Withdrawal"] = columns["withdrawal"]
            if columns["balance"] and not last_row["Balance"]:
                last_row["Balance"] = columns["balance"]

        return rows

    @staticmethod
    def _classify_row(words: List[Dict[str, Any]]) -> Dict[str, str]:
        tran_date_parts: List[str] = []
        value_date_parts: List[str] = []
        description_parts: List[str] = []
        cheque_parts: List[str] = []
        deposit = ""
        withdrawal = ""
        balance = ""

        for word in sorted(words, key=lambda w: w["x0"]):
            text = str(word["text"])
            x0 = float(word["x0"])
            x1 = float(word["x1"])

            if _AMOUNT_WORD_RE.match(text):
                distances = {
                    "deposit": abs(x1 - _DEPOSIT_COL_X1),
                    "withdrawal": abs(x1 - _WITHDRAWAL_COL_X1),
                    "balance": abs(x1 - _BALANCE_COL_X1),
                }
                nearest = min(distances, key=lambda k: distances[k])
                if nearest == "deposit":
                    deposit = text
                elif nearest == "withdrawal":
                    withdrawal = text
                else:
                    balance = text
                continue

            is_date_token = bool(_MONTH_RE.match(text) or _DAY_RE.match(text))

            if is_date_token and x0 < _TRAN_DATE_MAX_X0:
                tran_date_parts.append(text)
                continue

            if is_date_token and x0 < _VALUE_DATE_MAX_X0:
                value_date_parts.append(text)
                continue

            if x0 >= _CHEQUE_MIN_X0:
                cheque_parts.append(text)
            else:
                description_parts.append(text)

        return {
            "tran_date": " ".join(tran_date_parts),
            "value_date": " ".join(value_date_parts),
            "description": " ".join(description_parts),
            "cheque": " ".join(cheque_parts),
            "deposit": deposit,
            "withdrawal": withdrawal,
            "balance": balance,
        }

    @staticmethod
    def _is_header_line(line: str) -> bool:
        normalized = re.sub(r"[^a-z\s]", " ", line.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        marker_tokens = ["date", "value", "description", "cheque", "deposit", "withdrawal", "balance"]
        return all(token in normalized for token in marker_tokens)
