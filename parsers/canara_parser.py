"""Canara bank parser for statement format shown in provided sample."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Set, Tuple

from core.models import ParseResult
from core.pdf_loader import PageExtraction
from parsers.base_parser import BaseParser

# pdfplumber merges the Date column and amount columns onto one line, e.g.:
#   "01-04-2025 50,078.00 72,099.02"
#   "01-04-2025 MEHTA/CANARA//14965534779/ 31,500.00 40,599.02"
# Group 1 = date, group 2 = optional middle text, group 3 = amount, group 4 = balance.
_KEY_LINE_RE = re.compile(
    r"^(\d{2}[-/]\d{2}[-/]\d{4})"  # date (DD-MM-YYYY or DD/MM/YYYY)
    r"(.*?)"                          # optional middle particulars text (non-greedy)
    r"\s+([-]?\d[\d,]*\.\d{2})"      # transaction amount
    r"\s+([-]?\d[\d,]*\.\d{2})\s*$"  # running balance
)
# Each Canara transaction ends with a "Chq:" line (possibly with nothing after the colon).
_CHQ_LINE_RE = re.compile(r"^Chq\s*:", re.IGNORECASE)


class CanaraParser(BaseParser):
    key = "canara"
    display_name = "canara"

    def __init__(self, key: str = "canara", display_name: str = "canara") -> None:
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
            warnings.append("No Canara transaction rows were parsed from text content.")

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

        # ------------------------------------------------------------------ #
        # Pass 1 – tag every line                                             #
        # ------------------------------------------------------------------ #
        key_lines: List[Tuple[int, str, str, str, str]] = []  # (pos, date, mid, amt, bal)
        chq_positions: List[int] = []
        special_positions: Set[int] = set()
        closing_balance_row: Optional[Dict[str, str]] = None

        for i, line in enumerate(lines):
            if not line:
                continue

            if self._is_page_footer(line) or self._is_header_line(line):
                special_positions.add(i)
                continue

            if line.startswith("Opening Balance"):
                special_positions.add(i)
                balance = self._extract_last_amount(line)
                if balance:
                    row = self._empty_row()
                    row["Particulars"] = "Opening Balance"
                    row["Balance"] = balance
                    rows.append(row)
                    prev_balance = self._parse_decimal(balance)
                else:
                    warnings.append("Could not parse opening balance line.")
                continue

            if line.startswith("Closing Balance"):
                special_positions.add(i)
                balance = self._extract_last_amount(line)
                if balance:
                    closing_balance_row = self._empty_row()
                    closing_balance_row["Particulars"] = "Closing Balance"
                    closing_balance_row["Balance"] = balance
                    # NOTE: do not seed prev_balance here. Pass 1 runs to completion
                    # before Pass 2 classifies transactions, so setting prev_balance
                    # from the closing balance would corrupt the running balance used
                    # to classify the first key line on the page.
                else:
                    warnings.append("Could not parse closing balance line.")
                continue

            m = _KEY_LINE_RE.match(line)
            if m:
                date = m.group(1).replace("/", "-")
                mid = m.group(2).strip()
                amt = m.group(3)
                bal = m.group(4)
                key_lines.append((i, date, mid, amt, bal))
                continue

            if _CHQ_LINE_RE.match(line):
                chq_positions.append(i)

        # ------------------------------------------------------------------ #
        # Pass 2 – assemble transactions                                      #
        # ------------------------------------------------------------------ #
        for k_idx, (k_pos, date, mid_text, amount, balance) in enumerate(key_lines):
            prev_key_pos = key_lines[k_idx - 1][0] if k_idx > 0 else -1
            next_key_pos = key_lines[k_idx + 1][0] if k_idx + 1 < len(key_lines) else len(lines)

            # The CHQ line that follows this key line (transaction boundary).
            chq_end = next(
                (c for c in chq_positions if k_pos < c < next_key_pos),
                next_key_pos,  # fallback: no CHQ found; collect up to next key
            )

            # The CHQ line that ended the PREVIOUS transaction.
            chq_start_line = next(
                (c for c in reversed(chq_positions) if prev_key_pos < c < k_pos),
                None,
            )

            if chq_start_line is not None:
                collect_start = chq_start_line + 1
            else:
                # No previous CHQ – start after the last special element before k_pos.
                last_special = max(
                    (p for p in special_positions if p < k_pos), default=-1
                )
                collect_start = last_special + 1

            # Collect before-key and after-key text separately so that mid_text
            # can be inserted at the natural position between them.
            before: List[str] = []
            after: List[str] = []
            for i in range(collect_start, chq_end + 1):
                if i == k_pos or i in special_positions:
                    continue
                text = lines[i]
                if not text:
                    continue
                if i < k_pos:
                    before.append(text)
                else:
                    after.append(text)

            particulars_parts = before + ([mid_text] if mid_text else []) + after
            particulars = " ".join(particulars_parts).strip()

            deposit, withdrawal = self._classify_amount(amount, balance, prev_balance)

            row = {
                "Date": date,
                "Particulars": particulars,
                "Deposits": deposit,
                "Withdrawals": withdrawal,
                "Balance": balance,
            }
            rows.append(row)
            prev_balance = self._parse_decimal(balance)

        # Append Closing Balance after all transactions (preserves correct order).
        if closing_balance_row is not None:
            rows.append(closing_balance_row)

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

        current_balance = CanaraParser._parse_decimal(balance_value)
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
    def _extract_last_amount(line: str) -> str:
        matches = re.findall(r"[-]?\d[\d,]*\.\d{2}", line)
        return matches[-1] if matches else ""

    @staticmethod
    def _is_header_line(line: str) -> bool:
        normalized = re.sub(r"\s+", " ", line).strip().lower()
        marker_tokens = ["date", "particulars", "deposits", "withdrawals", "balance"]
        return all(token in normalized for token in marker_tokens)

    @staticmethod
    def _is_page_footer(line: str) -> bool:
        return bool(re.match(r"^page\s+\d+$", line.lower()))

    @staticmethod
    def _clean_line(line: str) -> str:
        cleaned = line
        cleaned = cleaned.replace("\ufffe", "-")
        cleaned = cleaned.replace("\u2010", "-").replace("\u2011", "-").replace("\u2012", "-")
        cleaned = cleaned.replace("\u2013", "-").replace("\u2014", "-")
        cleaned = cleaned.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
        cleaned = cleaned.replace("\ufeff", "")
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
