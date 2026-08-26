@echo off
REM Avvio GUI su Windows (o passa argomenti CLI: record / scan / demo)
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
if "%~1"=="" (
  python -m wattbike_logger gui
) else (
  python -m wattbike_logger %*
)
