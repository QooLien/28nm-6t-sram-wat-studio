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
  exit /b 1
)

if not exist "%PROJECT_ROOT%\wheelhouse" mkdir "%PROJECT_ROOT%\wheelhouse"
echo Downloading Windows wheels for the current Python version...
%PYTHON_CMD% -m pip download --only-binary=:all: --dest "%PROJECT_ROOT%\wheelhouse" -r "%PROJECT_ROOT%\requirements.txt"
if errorlevel 1 (
  echo Offline package preparation failed. Confirm internet access and Python version compatibility.
  exit /b 1
)

echo Offline packages are ready in: %PROJECT_ROOT%\wheelhouse
echo Copy the entire project, including wheelhouse, to the intranet PC.
exit /b 0
