@echo off
REM Avvio rapido Windows: attiva venv se presente e lancia il logger
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python -m wattbike_logger %*
