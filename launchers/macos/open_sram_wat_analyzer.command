#!/bin/zsh

# Always launch with the project-local Python environment.  Finder-launched
# .command files otherwise inherit the system Python, which does not contain
# the report/PNG export packages installed for this application.
SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h:h}"
PROJECT_PYTHON="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PROJECT_PYTHON}" ]]; then
  osascript -e 'display alert "HV28 SRAM Analysis" message "The project Python environment is missing. Reinstall the Mac dependencies before launching." as critical'
  exit 1
fi

cd "${PROJECT_ROOT}" || exit 1
exec "${PROJECT_PYTHON}" "${PROJECT_ROOT}/sram_wat_analyzer.py"
