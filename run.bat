@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM run.bat — IDSS Data Platform local launcher
REM
REM Schedule with Windows Task Scheduler:
REM   Action: Start a program
REM   Program: C:\Windows\System32\cmd.exe
REM   Arguments: /c "D:\My Files\LKL Reports\idss-data-platform\run.bat"
REM ─────────────────────────────────────────────────────────────────────────────

cd /d "D:\My Files\LKL Reports\idss-data-platform"

REM Create logs directory if missing
if not exist logs mkdir logs

REM Build a timestamped log filename: logs\pipeline_20260425.log
set LOGDATE=%date:~-4,4%%date:~-7,2%%date:~-10,2%
set LOGFILE=logs\pipeline_%LOGDATE%.log

echo [%date% %time%] Starting IDSS pipeline... >> %LOGFILE%

REM Activate virtual environment and run
call venv\Scripts\activate
python main.py >> %LOGFILE% 2>&1

set EXIT_CODE=%ERRORLEVEL%
echo [%date% %time%] Pipeline finished. Exit code: %EXIT_CODE% >> %LOGFILE%

REM Optional: alert on failure (requires BurntToast PowerShell module or similar)
if %EXIT_CODE% NEQ 0 (
    echo [%date% %time%] ERROR: Pipeline failed with exit code %EXIT_CODE% >> %LOGFILE%
)

exit /b %EXIT_CODE%
