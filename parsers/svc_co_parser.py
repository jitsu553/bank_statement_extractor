"""SVC Co-operative Bank parser for statement format shown in provided sample.

Like HDFC/Axis/BOB/BOI, SVC packs Debit and Credit into two columns of
identical shape (plain decimal numbers) that pdfplumber's plain text
extraction would collapse together with no delimiter - so which column a
printed number came from cannot be recovered from text alone. Rather than
guess from the running balance (fragile - see HdfcParser's docstring for
why that approach broke on a real statement), this parser reads each
page's word positions (`PageExtraction.words`) and assigns every word to
its actual table column by geometry:

    - Date is a left-aligned date token at a fixed x-position (~28); there
      is only one date column in this template, so no anchor comparison is
      needed to place it.
    - Particulars / Chq No are free text, split by a left-edge cutoff
      (~270) between the two columns.
    - Debit / Credit / Balance are right-aligned amounts, so wider numbers
      push their left edge further left - only the right edge (x1) stays
      constant per column (~379 / ~460 / ~525), so a plain amount is
      matched to whichever anchor its x1 is closest to. The Balance column
      additionally carries a standalone "CR"/"DR" suffix word right after
      the figure (e.g. "58151.53 CR"), which is recombined into one string
      to match how the PDF displays it.

These anchors were measured directly off this statement's header row and
data and are specific to this SVC template.

Each transaction's key row (Date + first Particulars fragment + Debit or
Credit amount + Balance) is sometimes followed by a continuation row before
the next key row, carrying the rest of the wrapped Particulars text - so,
like HdfcParser, continuation rows are simply merged into the transaction
most recently opened by a key row.

Unlike HDFC, SVC repeats the "Date Particulars Chq No Debit Credit Balance"
header row and a "Page N of 16" footer on every page, but (like HDFC/BOB)
does not repeat the customer/account info block - that only appears once,
before the very first header row on page 1. The whole document is
processed as one continuous row stream (not per page) so a transaction
wrapped across a page boundary is not split. An "Opening Balance b/f" row
right after the first header, and a "TOTAL:" row at the very end (followed
by an "End Of Report" marker and disclaimer notes), carry no date and are
recognised explicitly and emitted as their own rows rather than being
merged into a transaction's Particulars.

This module is intentionally self-contained (no shared helpers with
HdfcParser/AxisParser/BobParser/BoiParser/IciciParser/CanaraParser) so that
changes here can never affect their parsing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser

_DATE_WORD_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
_AMOUNT_WORD_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")
_BALANCE_SUFFIX_RE = re.compile(r"^(CR|DR)$", re.IGNORECASE)

# Column position anchors measured off this statement's header row/data
# (see module docstring for why x0 vs x1 is used per column).
_CHQNO_MIN_X0 = 270.0  # Chq No data sits at ~278; Particulars text never reaches that far
_DEBIT_COL_X1 = 379.0
_CREDIT_COL_X1 = 460.0
_BALANCE_COL_X1 = 525.0
_BALANCE_SUFFIX_MIN_X0 = 480.0

_ROW_TOP_TOLERANCE = 2.0  # points; groups words sharing a visual line

_PAGE_FOOTER_RE = re.compile(r"^PAGE\s+\d+\s+OF\s+\d+$", re.IGNORECASE)
_OPENING_BALANCE_RE = re.compile(r"^OPENING\s+BALANCE\b", re.IGNORECASE)
_TOTAL_RE = re.compile(r"^TOTAL\s*:", re.IGNORECASE)


class SvcCoParser(BaseParser):
    key = "svc_co"
    display_name = "svc_co"

    def __init__(self, key: str = "svc_co", display_name: str = "svc_co") -> None:
        self.key = key
        self.display_name = display_name

    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        headers = ["Date", "Particulars", "Chq No", "Debit", "Credit", "Balance"]
        warnings: List[str] = []

        # A transaction's continuation row can follow its key row across a
        # page boundary, so every page's rows are flattened into one
        # continuous stream before assembling transactions.
        table_rows: List[List[Dict[str, Any]]] = []
        for page in pages:
            table_rows.extend(self._group_words_into_rows(page.words))

        rows = self._assemble_transactions(table_rows)

        if not rows:
            warnings.append("No SVC Co-operative Bank transaction rows were parsed from text content.")

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

            if _PAGE_FOOTER_RE.match(row_text):
                continue

            columns = self._classify_row(words)

            if _OPENING_BALANCE_RE.match(row_text):
                rows.append(
                    {
                        "Date": "",
                        "Particulars": "Opening Balance",
                        "Chq No": "",
                        "Debit": "",
                        "Credit": "",
                        "Balance": columns["balance"],
                    }
                )
                last_row = None
                continue

            if _TOTAL_RE.match(row_text):
                rows.append(
                    {
                        "Date": "",
                        "Particulars": "Total",
                        "Chq No": "",
                        "Debit": columns["debit"],
                        "Credit": columns["credit"],
                        "Balance": "",
                    }
                )
                last_row = None
                continue

            if columns["date"]:
                row = {
                    "Date": columns["date"],
                    "Particulars": columns["particulars"],
                    "Chq No": columns["chqno"],
                    "Debit": columns["debit"],
                    "Credit": columns["credit"],
                    "Balance": columns["balance"],
                }
                rows.append(row)
                last_row = row
                continue

            # Not a key row: a wrapped continuation of the current
            # transaction's Particulars.
            if last_row is None:
                continue

            if columns["particulars"]:
                last_row["Particulars"] = (last_row["Particulars"] + " " + columns["particulars"]).strip()
            if columns["chqno"]:
                last_row["Chq No"] = (last_row["Chq No"] + " " + columns["chqno"]).strip()
            if columns["debit"] and not last_row["Debit"]:
                last_row["Debit"] = columns["debit"]
            if columns["credit"] and not last_row["Credit"]:
                last_row["Credit"] = columns["credit"]
            if columns["balance"] and not last_row["Balance"]:
                last_row["Balance"] = columns["balance"]

        return rows

    @staticmethod
    def _classify_row(words: List[Dict[str, Any]]) -> Dict[str, str]:
        date = ""
        particulars_parts: List[str] = []
        chqno_parts: List[str] = []
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

            if x0 >= _CHQNO_MIN_X0:
                chqno_parts.append(text)
            else:
                particulars_parts.append(text)

        balance = " ".join(part for part in (balance_amount, balance_suffix) if part)

        return {
            "date": date,
            "particulars": " ".join(particulars_parts),
            "chqno": " ".join(chqno_parts),
            "debit": debit,
            "credit": credit,
            "balance": balance,
        }

    @staticmethod
    def _is_header_line(line: str) -> bool:
        normalized = re.sub(r"[^a-z\s]", " ", line.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        marker_tokens = ["date", "particulars", "chq", "no", "debit", "credit", "balance"]
        return all(token in normalized for token in marker_tokens)
