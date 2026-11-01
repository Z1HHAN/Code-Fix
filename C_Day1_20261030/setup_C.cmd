@echo off
setlocal
set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
if exist ".runtime\python314\tools\python.exe" (
  ".runtime\python314\tools\python.exe" -m venv .venv
) else (
    py -3.14 -m venv .venv
)
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 14) else 1)"
if errorlevel 1 goto failed
if exist "wheelhouse\pytest-8.3.5-py3-none-any.whl" (
  ".venv\Scripts\python.exe" -m pip install --no-index --find-links=wheelhouse -r requirements-lock.txt
) else (
  ".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
)
if errorlevel 1 goto failed
echo Ready. Double-click run_C.cmd to run the two checks.
pause
exit /b 0
:failed
echo Setup failed. Python 3.14 is required. Keep this error text.
pause
exit /b 1
