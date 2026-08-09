#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' "Python 3 was not found."
  printf 'Press Return to close this window...'
  read -r _
  exit 1
fi

python3 "$PROJECT_ROOT/tools/sync_project.py" --interactive
printf '\nPress Return to close this window...'
read -r _
