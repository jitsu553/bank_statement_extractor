"""Deutsche Bank parser for statement format shown in provided sample.

Like the other single-amount-column banks handled so far, Deutsche Bank
packs Withdrawal and Deposit into two columns of identical shape (plain
decimal numbers) that pdfplumber's plain text extraction would collapse
together with no delimiter - so which column a printed number came from
cannot be recovered from text alone. Rather than guess from the running
balance (fragile - see HdfcParser's docstring for why that approach broke
on a real statement), this parser reads each page's word positions
(`PageExtraction.words`) and assigns every word to its actual table column
by geometry:

    - Date / Value Date are left-aligned date tokens; whichever of the two
      fixed x-positions a token's left edge (x0) sits closer to decides the
      column.
    - Narration / Ref. Chq No. are free text, split by a left-edge cutoff
      between the two columns.
    - Withdrawal / Deposit / Closing Balance are right-aligned amounts, so
      wider numbers push their left edge further left - only the right
      edge (x1) stays constant per column, so a plain amount is matched to
      whichever anchor its x1 is closest to. The Closing Balance column
      additionally carries a standalone "Cr"/"Dr" suffix word right after
      the figure, which is recombined into one string to match how the PDF
      displays it.

These anchors were measured directly off this statement's header row and
data and are specific to this Deutsche Bank template. Note the PDF this was
built from renders at an unusually large coordinate scale (x/y values in
the tens of thousands rather than the few hundred typical of an A4 page at
72 DPI) - the anchors below are simply whatever pdfplumber reported for
*this* document, not a general-purpose unit.

Each transaction's key row (Date + first Narration fragment + Ref./Chq No.
+ Value Date + Withdrawal or Deposit + Closing Balance) is sometimes
followed by continuation row(s) carrying the rest of the wrapped
Narration - handled the same way as HdfcParser (continuation rows are
merged into the transaction most recently opened by a key row).

An "Opening Balance" row and a "Closing Balance:" summary row both carry no
date and are recognised explicitly. The Closing Balance row is a special
case worth calling out: unlike every real transaction row, its three
amounts (Withdrawal total, Deposit total, Balance) are not aligned to the
same column x-positions as the rest of the table, which defeats the
nearest-anchor matching used everywhere else - so for this one row, the
three numbers are read strictly in left-to-right order instead (matching
the header's own column order) rather than by position.

This is currently only known to produce a single page, but the same
row-stream flattening and header-detection approach used by the other bank
parsers is applied anyway so a multi-page statement in the same template
would not silently misparse.

This module is intentionally self-contained (no shared helpers with
HdfcParser/AxisParser/BobParser/BoiParser/SvcCoParser/
StandardCharteredParser/IciciParser/CanaraParser) so that changes here can
never affect their parsing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser

_DATE_WORD_RE = re.compile(r"^\d{2}-\d{2}-\d{2}$")
_AMOUNT_WORD_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")
_BALANCE_SUFFIX_RE = re.compile(r"^(CR|DR)$", re.IGNORECASE)

# Column position anchors measured off this statement's header row/data
# (see module docstring for why x0 vs x1 is used per column, and for the
# unusually large coordinate scale).
_TRAN_DATE_COL_X0 = 1930.0
_VALUE_DATE_COL_X0 = 13076.9
_NARRATION_MAX_X0 = 9000.0  # Ref./Chq No. data starts ~9130
_REF_CHQ_MAX_X0 = 12500.0  # Value Date data starts ~13077
_WITHDRAWAL_COL_X1 = 17050.0
_DEPOSIT_COL_X1 = 19850.0
_BALANCE_COL_X1 = 22250.0
_BALANCE_SUFFIX_MIN_X0 = 22000.0

_ROW_TOP_TOLERANCE = 50.0  # this template's coordinate scale is ~40x a typical A4 page

_OPENING_BALANCE_RE = re.compile(r"^OPENING\s+BALANCE\b", re.IGNORECASE)
_CLOSING_BALANCE_RE = re.compile(r"^CLOSING\s+BALANCE\s*:", re.IGNORECASE)


class DeutscheParser(BaseParser):
    key = "deutsche"
    display_name = "deutsche"

    def __init__(self, key: str = "deutsche", display_name: str = "deutsche") -> None:
        self.key = key
        self.display_name = display_name

    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        headers = ["Date", "Narration", "Ref. Chq No.", "Value Date", "Withdrawl", "Deposit", "Closing Balance"]
        warnings: List[str] = []

        # A transaction's continuation rows can follow its key row across a
        # page boundary, so every page's rows are flattened into one
        # continuous stream before assembling transactions.
        table_rows: List[List[Dict[str, Any]]] = []
        for page in pages:
            table_rows.extend(self._group_words_into_rows(page.words))

        rows = self._assemble_transactions(table_rows)

        if not rows:
            warnings.append("No Deutsche Bank transaction rows were parsed from text content.")

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

            if self._is_header_line(row_text):
                in_table = True
                continue

            if not in_table:
                continue

            if _OPENING_BALANCE_RE.match(row_text):
                columns = self._classify_row(words)
                rows.append(
                    {
                        "Date": columns["date"],
                        "Narration": "Opening Balance",
                        "Ref. Chq No.": "",
                        "Value Date": "",
                        "Withdrawl": "",
                        "Deposit": "",
                        "Closing Balance": columns["balance"],
                    }
                )
                last_row = None
                continue

            if _CLOSING_BALANCE_RE.match(row_text):
                # This summary row's amounts are not aligned to the regular
                # column positions (see module docstring), so read the three
                # numbers strictly in left-to-right order instead of by
                # nearest-anchor.
                amounts = [str(w["text"]) for w in sorted(words, key=lambda w: w["x0"]) if _AMOUNT_WORD_RE.match(str(w["text"]))]
                suffix = next((str(w["text"]).upper() for w in words if _BALANCE_SUFFIX_RE.match(str(w["text"]))), "")
                withdrawal_total = amounts[0] if len(amounts) > 0 else ""
                deposit_total = amounts[1] if len(amounts) > 1 else ""
                balance_total = amounts[2] if len(amounts) > 2 else ""
                rows.append(
                    {
                        "Date": "",
                        "Narration": "Closing Balance",
                        "Ref. Chq No.": "",
                        "Value Date": "",
                        "Withdrawl": withdrawal_total,
                        "Deposit": deposit_total,
                        "Closing Balance": " ".join(part for part in (balance_total, suffix) if part),
                    }
                )
                last_row = None
                continue

            columns = self._classify_row(words)

            if columns["date"]:
                row = {
                    "Date": columns["date"],
                    "Narration": columns["narration"],
                    "Ref. Chq No.": columns["ref_chq_no"],
                    "Value Date": columns["value_date"],
                    "Withdrawl": columns["withdrawal"],
                    "Deposit": columns["deposit"],
                    "Closing Balance": columns["balance"],
                }
                rows.append(row)
                last_row = row
                continue

            # Not a key row: a wrapped continuation of the current
            # transaction's Narration.
            if last_row is None:
                continue

            if columns["narration"]:
                last_row["Narration"] = (last_row["Narration"] + " " + columns["narration"]).strip()
            if columns["ref_chq_no"]:
                last_row["Ref. Chq No."] = (last_row["Ref. Chq No."] + " " + columns["ref_chq_no"]).strip()
            if columns["withdrawal"] and not last_row["Withdrawl"]:
                last_row["Withdrawl"] = columns["withdrawal"]
            if columns["deposit"] and not last_row["Deposit"]:
                last_row["Deposit"] = columns["deposit"]
            if columns["balance"] and not last_row["Closing Balance"]:
                last_row["Closing Balance"] = columns["balance"]

        return rows

    @staticmethod
    def _classify_row(words: List[Dict[str, Any]]) -> Dict[str, str]:
        date = ""
        value_date = ""
        narration_parts: List[str] = []
        ref_chq_parts: List[str] = []
        withdrawal = ""
        deposit = ""
        balance_amount = ""
        balance_suffix = ""

        for word in sorted(words, key=lambda w: w["x0"]):
            text = str(word["text"])
            x0 = float(word["x0"])
            x1 = float(word["x1"])

            if _DATE_WORD_RE.match(text):
                if abs(x0 - _TRAN_DATE_COL_X0) <= abs(x0 - _VALUE_DATE_COL_X0):
                    date = text
                else:
                    value_date = text
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
                    balance_amount = text
                continue

            if _BALANCE_SUFFIX_RE.match(text) and x0 >= _BALANCE_SUFFIX_MIN_X0:
                balance_suffix = text.upper()
                continue

            if x0 < _NARRATION_MAX_X0:
                narration_parts.append(text)
            elif x0 < _REF_CHQ_MAX_X0:
                ref_chq_parts.append(text)
            else:
                narration_parts.append(text)

        balance = " ".join(part for part in (balance_amount, balance_suffix) if part)

        return {
            "date": date,
            "value_date": value_date,
            "narration": " ".join(narration_parts),
            "ref_chq_no": " ".join(ref_chq_parts),
            "withdrawal": withdrawal,
            "deposit": deposit,
            "balance": balance,
        }

    @staticmethod
    def _is_header_line(line: str) -> bool:
        normalized = re.sub(r"[^a-z\s]", " ", line.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        marker_tokens = ["date", "narration", "value", "deposit", "closing", "balance"]
        return all(token in normalized for token in marker_tokens)
