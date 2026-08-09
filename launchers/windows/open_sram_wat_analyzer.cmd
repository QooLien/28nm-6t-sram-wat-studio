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
  echo Python 3 was not found.
  echo Install 64-bit Python 3.11 to 3.13 and enable the py launcher or Add Python to PATH.
  echo See INTRANET_PC_SETUP.md for the company intranet/offline installation procedure.
  pause
  exit /b 1
)

%PYTHON_CMD% -c "import tkinter, reportlab, svglib, rlPyCairo, openpyxl" >nul 2>&1
if errorlevel 1 (
  echo Required Python packages are missing. Starting the dependency installer...
  call "%PROJECT_ROOT%\launchers\windows\install_dependencies.cmd"
  if errorlevel 1 (
    echo Dependency installation failed.
    echo For an intranet PC, copy the wheelhouse folder prepared on an internet-connected PC.
    echo See INTRANET_PC_SETUP.md.
    pause
    exit /b 1
  )
)

%PYTHON_CMD% "%PROJECT_ROOT%\sram_wat_analyzer.py"
if errorlevel 1 pause
endlocal
