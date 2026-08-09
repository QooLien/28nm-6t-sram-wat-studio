@echo off
setlocal
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
  echo Python 3 was not found. Install 64-bit Python first.
  exit /b 1
)

%PYTHON_CMD% -m pip --version >nul 2>&1
if errorlevel 1 (
  echo pip is unavailable. Reinstall Python with pip enabled.
  exit /b 1
)

if exist "%PROJECT_ROOT%\wheelhouse\*.whl" (
  echo Installing from the local offline wheelhouse...
  %PYTHON_CMD% -m pip install --no-index --find-links "%PROJECT_ROOT%\wheelhouse" -r "%PROJECT_ROOT%\requirements.txt"
) else (
  echo No wheelhouse was found. Attempting installation from the configured Python package index...
  %PYTHON_CMD% -m pip install -r "%PROJECT_ROOT%\requirements.txt"
)
if errorlevel 1 exit /b 1

%PYTHON_CMD% -c "import tkinter, reportlab, svglib, rlPyCairo, openpyxl; print('HV28 SRAM dependencies verified')"
if errorlevel 1 exit /b 1

echo Installation completed successfully.
exit /b 0
