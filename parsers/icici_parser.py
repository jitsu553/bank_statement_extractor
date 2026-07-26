"""ICICI bank parser for statement format shown in provided sample.

ICICI internet-banking statements wrap the Particulars column across
multiple text lines per transaction, similar in spirit to the Canara layout,
but WITHOUT an explicit line separator (Canara uses a trailing "Chq:" line).
A typical entry looks like this once pdfplumber flattens it to text:

    UPI/vpa/name/BANK                                             (before)
    03-04-2025 LIMITE/509353458530/ICI1aac917a647445968a4519 112.00 61,447.00  (key line)
    e1b9fca548/                                                   (after)

The DATE + trailing amount(s) always land on one "key line", but the
Particulars text wrapped above and below it is otherwise indistinguishable
from plain text. Since there is no delimiter, a non-key line following a key
line is treated as a *continuation* of the current transaction's Particulars
unless it starts with a recognised transaction-type prefix (UPI/, NEFT-,
MMT/, BIL/, ...), in which case it is treated as the start of the NEXT
transaction's Particulars instead.

This module is intentionally self-contained (no shared helpers with
CanaraParser) so that changes here can never affect Canara parsing.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Tuple

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser

# Group 1 = date, group 2 = optional mid text (mode label like "NET BANKING"
# or a wrapped Particulars fragment), group 3 = first amount, group 4 =
# optional second amount. The opening "B/F" row only carries a balance
# (group 4 is absent); every other row carries amount + balance.
_KEY_LINE_RE = re.compile(
    r"^(\d{2}[-/]\d{2}[-/]\d{4})"        # date (DD-MM-YYYY or DD/MM/YYYY)
    r"(.*?)"                              # optional mid text (non-greedy)
    r"\s+([-]?\d[\d,]*\.\d{2})"           # first amount
    r"(?:\s+([-]?\d[\d,]*\.\d{2}))?"      # optional second amount (balance)
    r"\s*$"
)

# A line starting with one of these known transaction-type codes marks the
# start of a NEW transaction's Particulars block. Anything else found after
# a key line is treated as a wrapped continuation of that same transaction.
_NEW_BLOCK_RE = re.compile(
    r"^(UPI|NEFT|RTGS|IMPS|MMT|BIL|NFS|CMS|BR|ACH|ATM|POS|ECOM|VPS|CHQ|CASH|SGB|MAB|INTT|INF|IB)[/\-]",
    re.IGNORECASE,
)

# Final summary row, e.g. "TOTAL 20,24,734.15 20,51,249.26 35,043.89"
# (total deposits, total withdrawals, closing balance).
_TOTAL_LINE_RE = re.compile(
    r"^TOTAL\s+([-]?\d[\d,]*\.\d{2})\s+([-]?\d[\d,]*\.\d{2})\s+([-]?\d[\d,]*\.\d{2})\s*$",
    re.IGNORECASE,
)


class IciciParser(BaseParser):
    key = "icici"
    display_name = "icici"

    def __init__(self, key: str = "icici", display_name: str = "icici") -> None:
        self.key = key
        self.display_name = display_name

    def parse(self, pages: Iterable[PageExtraction]) -> ParseResult:
        headers = ["Date", "Particulars", "Deposits", "Withdrawals", "Balance"]
        rows: List[Dict[str, str]] = []
        warnings: List[str] = []

        prev_balance: Optional[Decimal] = None

        for page in pages:
            page_rows, prev_balance, page_warnings = self._parse_page(page.text, prev_balance)
            rows.extend(page_rows)
            warnings.extend(page_warnings)

        if not rows:
            warnings.append("No ICICI transaction rows were parsed from text content.")

        return ParseResult(
            headers=headers,
            rows=rows,
            warnings=warnings,
            parser_name=self.display_name,
        )

    def _parse_page(
        self,
        page_text: str,
        prev_balance: Optional[Decimal],
    ) -> Tuple[List[Dict[str, str]], Optional[Decimal], List[str]]:
        warnings: List[str] = []
        rows: List[Dict[str, str]] = []
        lines = [self._clean_line(line) for line in page_text.splitlines()]

        # The transaction table is preceded by account-summary text (page 1
        # of the statement, and preamble on the first page containing rows)
        # which we skip entirely until the real column header is seen.
        in_table = False
        buffer: List[str] = []  # Particulars lines collected for the NEXT key line.
        last_row: Optional[Dict[str, str]] = None  # Most recent transaction row, for continuations.

        for line in lines:
            if not line:
                continue

            if self._is_header_line(line):
                in_table = True
                continue

            if self._is_footer_line(line):
                continue

            if not in_table:
                continue

            total_match = _TOTAL_LINE_RE.match(line)
            if total_match:
                row = self._empty_row()
                row["Particulars"] = "Total / Closing Balance"
                row["Deposits"] = total_match.group(1)
                row["Withdrawals"] = total_match.group(2)
                row["Balance"] = total_match.group(3)
                rows.append(row)
                last_row = None
                continue

            key_match = _KEY_LINE_RE.match(line)
            if key_match:
                date = key_match.group(1).replace("/", "-")
                mid = key_match.group(2).strip()
                num1 = key_match.group(3)
                num2 = key_match.group(4)

                particulars = " ".join(buffer + ([mid] if mid else [])).strip()
                buffer = []

                if num2 is None:
                    # Opening / brought-forward row: only a balance, no amount.
                    row = {
                        "Date": date,
                        "Particulars": particulars,
                        "Deposits": "",
                        "Withdrawals": "",
                        "Balance": num1,
                    }
                    rows.append(row)
                    last_row = row
                    prev_balance = self._parse_decimal(num1)
                    continue

                amount, balance = num1, num2
                deposit, withdrawal = self._classify_amount(amount, balance, prev_balance)

                row = {
                    "Date": date,
                    "Particulars": particulars,
                    "Deposits": deposit,
                    "Withdrawals": withdrawal,
                    "Balance": balance,
                }
                rows.append(row)
                last_row = row
                prev_balance = self._parse_decimal(balance)
                continue

            # Not a key/header/footer/total line: either the start of the
            # next transaction's Particulars, or a wrapped continuation of
            # the previous one.
            if last_row is not None and not _NEW_BLOCK_RE.match(line):
                last_row["Particulars"] = (last_row["Particulars"] + " " + line).strip()
            else:
                buffer.append(line)

        return rows, prev_balance, warnings

    @staticmethod
    def _classify_amount(
        amount_value: str,
        balance_value: str,
        prev_balance: Optional[Decimal],
    ) -> Tuple[str, str]:
        if amount_value.startswith("-"):
            return "", amount_value

        if prev_balance is None:
            return "", amount_value

        current_balance = IciciParser._parse_decimal(balance_value)
        if current_balance is None:
            return "", amount_value

        if current_balance >= prev_balance:
            return amount_value, ""
        return "", amount_value

    @staticmethod
    def _parse_decimal(value: str) -> Optional[Decimal]:
        cleaned = value.replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _is_header_line(line: str) -> bool:
        normalized = re.sub(r"[^a-z\s]", " ", line.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        marker_tokens = ["date", "mode", "particulars", "deposits", "withdrawals", "balance"]
        return all(token in normalized for token in marker_tokens)

    @staticmethod
    def _is_footer_line(line: str) -> bool:
        return bool(re.match(r"^page\s+\d+\s+of\s+\d+$", line.strip().lower()))

    @staticmethod
    def _clean_line(line: str) -> str:
        cleaned = line
        cleaned = cleaned.replace("￾", "-")
        cleaned = cleaned.replace("‐", "-").replace("‑", "-").replace("‒", "-")
        cleaned = cleaned.replace("–", "-").replace("—", "-")
        cleaned = cleaned.replace("​", "").replace("‌", "").replace("‍", "")
        cleaned = cleaned.replace("﻿", "")
        return cleaned.strip()

    @staticmethod
    def _empty_row() -> Dict[str, str]:
        return {
            "Date": "",
            "Particulars": "",
            "Deposits": "",
            "Withdrawals": "",
            "Balance": "",
        }
