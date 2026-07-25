@echo off
setlocal

REM Build single-file Windows executable.
pyinstaller --onefile --windowed --name BankStatementExtractor --add-data "config;config" main.py

if %errorlevel% neq 0 (
  echo Build failed.
  exit /b %errorlevel%
)

echo Build complete. Output: dist\BankStatementExtractor.exe
endlocal
