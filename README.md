# Bank Statement Extractor

Offline desktop utility to convert bank statement PDFs into XLSX while preserving extracted column names and values.

## MVP Scope

- Python-first implementation
- Offline use only
- Windows 7+ target (use Python 3.8 for build/runtime)
- Text-based PDFs only (no OCR in MVP)
- No database
- Logs to `log.txt` next to executable
- Minimal desktop UI for selecting input PDF and output XLSX

## How It Works

1. User selects a PDF.
2. App detects bank from signature text on first pages.
3. App selects parser (or user can override parser).
4. Parser extracts rows/tables page by page.
5. App writes extracted data to XLSX.
6. Conversion details are appended to `log.txt`.

## Setup (Developer)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## Build EXE

Install PyInstaller first:

```powershell
pip install pyinstaller
```

Then run:

```powershell
build.bat
```

Output will be created in `dist/BankStatementExtractor.exe`.

## Add New Bank Parser

1. Create parser in `parsers/` implementing `BaseParser`.
2. Register parser in `parsers/registry.py`.
3. Add signature patterns in `config/bank_signatures.json` mapping to parser key.
4. Test with sample PDFs.

Dropdown behavior:
- The Bank dropdown shows values exactly as the keys in `config/bank_signatures.json`.
- If you want a specific dropdown text, set that exact text as the JSON key.

## Notes for Windows 7

- Build and test with Python 3.8.x.
- Ensure Windows 7 SP1 has required updates.
- Keep dependency versions compatible with Python 3.8.
