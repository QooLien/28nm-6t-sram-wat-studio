@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0\..\.."
set "PROJECT_ROOT=%CD%"

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
  where python >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo Python 3 was not found.
  pause
  exit /b 1
)

%PYTHON_CMD% "%PROJECT_ROOT%\tools\sync_project.py" --interactive
echo.
pause
endlocal
