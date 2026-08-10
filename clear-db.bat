@echo off
REM Selective DB reset: wipe index/graph/doc data, keep workspace/provider/code-host/repo.
setlocal
cd /d "%~dp0"

set "PY=%~dp0backend\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%~dp0scripts\clear_db.py" %*

echo.
pause
