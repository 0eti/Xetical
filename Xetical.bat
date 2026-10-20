@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo First run: setting up Python environment...
  python -m venv .venv || goto :err
)
rem Install again whenever requirements.txt changed or the last install did not finish
fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1 || (
  echo Installing components, this takes 1-2 minutes...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :err
  copy /y requirements.txt ".venv\requirements.installed" >nul
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.pyw"
exit /b
:err
echo Setup failed. Make sure Python 3.10 or newer is installed and you are online.
pause
