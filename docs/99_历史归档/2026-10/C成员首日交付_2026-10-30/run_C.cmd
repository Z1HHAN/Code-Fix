@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No project environment found. Run setup_C.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\run_c_checks.py"
set "CHECK_EXIT=%ERRORLEVEL%"
echo.
if "%CHECK_EXIT%"=="0" (
  echo C checks completed. The bad version failing 1 test is EXPECTED.
  echo Open the newest runs folder for evidence.
) else (
  echo C checks did not meet expectations. Keep the logs for diagnosis.
)
pause
exit /b %CHECK_EXIT%