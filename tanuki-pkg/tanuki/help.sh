#!/bin/bash
# TanukiPKG - cross-distro .deb package manager
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_PATH="$PROJECT_ROOT/python:$PROJECT_ROOT/tanuki/python"

PYTHON="${TANUKI_PYTHON:-python3}"

if ! command -v "$PYTHON" &>/dev/null; then
    echo "Error: Python ($PYTHON) not found."
    exit 1
fi

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$PYTHON_PATH"
exec "$PYTHON" -m main -h
