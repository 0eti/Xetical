@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo First run: installing components, this takes 1-2 minutes...
  python -m venv .venv || goto :err
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :err
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.pyw"
exit /b
:err
echo Setup failed. Make sure Python 3.10 or newer is installed.
pause
