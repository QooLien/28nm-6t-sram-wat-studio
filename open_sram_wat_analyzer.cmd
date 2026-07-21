@echo off
cd /d "%~dp0"
python -c "import reportlab, svglib" >nul 2>&1
if errorlevel 1 (
  echo Installing HTML chart export components...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Could not install the HTML chart export components.
    pause
    exit /b 1
  )
)
python sram_wat_analyzer.py
if errorlevel 1 pause
