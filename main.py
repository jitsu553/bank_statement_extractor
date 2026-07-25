"""Desktop entry point for offline bank statement conversion."""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from core.bank_detector import detect_bank, get_bank_dropdown_values, get_parser_for_bank_key
from core.converter import convert_pdf_to_xlsx
from utils.logging_utils import setup_logger
from utils.runtime import get_runtime_dir


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Bank Statement Extractor")
        self.root.geometry("680x340")
        self.root.resizable(False, False)

        self.logger = setup_logger()
        self.bank_options = get_bank_dropdown_values()

        self.pdf_path_var = tk.StringVar()
        self.output_path_var = tk.StringVar()
        self.detected_bank_var = tk.StringVar(value="Not detected")
        initial_bank = self.bank_options[0] if self.bank_options else "unknown"
        self.selected_bank_var = tk.StringVar(value=initial_bank)
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.StringVar(value="")
        self.open_after_var = tk.BooleanVar(value=True)

        self._build_ui()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Input PDF").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.pdf_path_var, width=62).grid(row=1, column=0, sticky="we", padx=(0, 8))
        ttk.Button(frame, text="Browse", command=self.select_pdf).grid(row=1, column=1, sticky="e")

        ttk.Label(frame, text="Detected Bank").grid(row=2, column=0, sticky="w", pady=(12, 0))
        ttk.Label(frame, textvariable=self.detected_bank_var).grid(row=3, column=0, sticky="w")

        ttk.Label(frame, text="Bank").grid(row=4, column=0, sticky="w", pady=(12, 0))
        bank_combo = ttk.Combobox(
            frame,
            textvariable=self.selected_bank_var,
            values=self.bank_options,
            state="readonly",
            width=30,
        )
        bank_combo.grid(row=5, column=0, sticky="w")

        ttk.Label(frame, text="Output XLSX").grid(row=6, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(frame, textvariable=self.output_path_var, width=62).grid(row=7, column=0, sticky="we", padx=(0, 8))
        ttk.Button(frame, text="Save As", command=self.select_output).grid(row=7, column=1, sticky="e")

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=8, column=0, columnspan=2, sticky="we", pady=(18, 0))
        ttk.Button(btn_row, text="Convert", command=self.convert).pack(side="left")
        ttk.Checkbutton(
            btn_row,
            text="Open file after conversion",
            variable=self.open_after_var,
        ).pack(side="left", padx=(12, 0))
        ttk.Button(frame, text="Open Log", command=self.open_log_hint).grid(row=8, column=1, sticky="e", pady=(18, 0))

        ttk.Label(frame, textvariable=self.status_var).grid(row=9, column=0, sticky="w", pady=(18, 0))
        ttk.Label(frame, textvariable=self.progress_var).grid(row=10, column=0, sticky="w", pady=(6, 0))

        frame.columnconfigure(0, weight=1)

    def select_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title="Select bank statement PDF",
            filetypes=[("PDF files", "*.pdf")],
        )
        if not path:
            return

        self.pdf_path_var.set(path)

        if not self.output_path_var.get().strip():
            suggested = os.path.splitext(path)[0] + ".xlsx"
            self.output_path_var.set(suggested)

        self.detect_bank()

    def select_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Select output XLSX",
            defaultextension=".xlsx",
            filetypes=[("Excel Workbook", "*.xlsx")],
        )
        if path:
            self.output_path_var.set(path)

    def detect_bank(self) -> None:
        pdf_path = self.pdf_path_var.get().strip()
        if not pdf_path:
            return

        try:
            detection = detect_bank(pdf_path)
            self.detected_bank_var.set(
                f"{detection.bank_display_name} ({detection.confidence:.2f})"
            )
            if detection.bank_key in self.bank_options:
                self.selected_bank_var.set(detection.bank_key)
            self.status_var.set("Bank detection complete")
        except Exception as error:  # pragma: no cover - UI safety path
            self.logger.exception("Bank detection failed")
            self.detected_bank_var.set("Unknown Bank")
            self.status_var.set(f"Detection failed: {error}")

    @staticmethod
    def _stamped_path(path: str) -> str:
        """Return *path* with a DD_MM_YYYY_hh_mm_ss timestamp inserted before the extension."""
        root_part, ext = os.path.splitext(path)
        stamp = datetime.now().strftime("%d_%m_%Y_%H_%M_%S")
        return f"{root_part}_{stamp}{ext}"

    @staticmethod
    def _open_file(path: str) -> None:
        """Open *path* with the OS default application."""
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def convert(self) -> None:
        pdf_path = self.pdf_path_var.get().strip()
        output_path = self.output_path_var.get().strip()

        if not pdf_path or not os.path.exists(pdf_path):
            messagebox.showerror("Missing Input", "Please select a valid PDF file.")
            return

        if not output_path:
            messagebox.showerror("Missing Output", "Please select an output XLSX path.")
            return

        # If the target file already exists, add a timestamp suffix to avoid overwriting.
        if os.path.exists(output_path):
            output_path = self._stamped_path(output_path)
            self.output_path_var.set(output_path)

        self.status_var.set("Converting...")
        self.progress_var.set("")
        self.root.update_idletasks()

        def on_progress(current: int, total: int) -> None:
            self.progress_var.set(f"Processing page {current}/{total}")
            self.root.update_idletasks()

        try:
            selected_bank_key = self.selected_bank_var.get().strip()
            selected_parser_key = get_parser_for_bank_key(selected_bank_key)

            result = convert_pdf_to_xlsx(
                pdf_path=pdf_path,
                output_xlsx_path=output_path,
                logger=self.logger,
                forced_parser_key=selected_parser_key,
                progress_callback=on_progress,
            )
            if result.success:
                self.status_var.set("Conversion completed successfully")
                messagebox.showinfo("Success", result.message)
                if self.open_after_var.get():
                    self._open_file(output_path)
            else:
                self.status_var.set("Conversion failed")
                messagebox.showwarning("Conversion Failed", result.message)

            if result.parse_result.warnings:
                warning_text = "\n".join(result.parse_result.warnings)
                messagebox.showwarning("Warnings", warning_text)

        except Exception as error:  # pragma: no cover - UI safety path
            self.logger.error("Unhandled conversion error: %s", error)
            self.logger.error(traceback.format_exc())
            self.status_var.set("Conversion failed")
            messagebox.showerror("Error", f"Conversion failed: {error}")

    def open_log_hint(self) -> None:
        messagebox.showinfo(
            "Log Location",
            f"Log file: {os.path.join(get_runtime_dir(), 'log.txt')}",
        )


def main() -> None:
    root = tk.Tk()
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
