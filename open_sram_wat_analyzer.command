#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

fail() {
  printf '\n%s\n' "$1"
  printf 'Press Return to close this window...'
  read -r _
  exit 1
}

if ! command -v python3 >/dev/null 2>&1; then
  fail "Python 3 was not found. Install Python 3 from https://www.python.org/downloads/macos/ and try again."
fi

if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
  fail "This Python installation does not include Tkinter. Install the macOS Python package from python.org and try again."
fi

if [ ! -x ".venv/bin/python" ]; then
  printf '%s\n' "Creating the local macOS Python environment..."
  python3 -m venv .venv || fail "Could not create the local Python environment."
fi

PYTHON="$SCRIPT_DIR/.venv/bin/python"

if ! "$PYTHON" -c 'import reportlab, svglib, openpyxl, _rl_renderPM' >/dev/null 2>&1; then
  printf '%s\n' "Installing report export components..."
  "$PYTHON" -m pip install -r requirements.txt || fail "Could not install the report export components. Check the internet connection and try again."
fi

printf '%s\n' "Opening HV28 SRAM Analysis..."
"$PYTHON" sram_wat_analyzer.py
status=$?

if [ "$status" -ne 0 ]; then
  fail "HV28 SRAM Analysis exited with an error (status $status)."
fi
