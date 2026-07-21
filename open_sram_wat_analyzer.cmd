@echo off
cd /d "%~dp0"
python sram_wat_analyzer.py
if errorlevel 1 pause
