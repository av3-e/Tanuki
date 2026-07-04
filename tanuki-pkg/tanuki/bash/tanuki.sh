#!/bin/bash
set -e

TANUKI_ROOT="${TANUKI_ROOT:-/var/lib/tanuki}"
TANUKI_PYTHON="${TANUKI_PYTHON:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_PATH="$PROJECT_ROOT/tanuki/python:$PROJECT_ROOT/python"
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$PYTHON_PATH"

if ! command -v "$TANUKI_PYTHON" &> /dev/null; then
    echo "Error: Python ($TANUKI_PYTHON) not found."
    echo ""
    echo "Install python for your distribution:"
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        case "${ID_LIKE:-$ID}" in
            arch|archlinux)
                echo "  sudo pacman -S python"
                ;;
            fedora|rhel|centos)
                echo "  sudo dnf install python3"
                ;;
            alpine)
                echo "  sudo apk add python3"
                ;;
            suse|opensuse*)
                echo "  sudo zypper install python3"
                ;;
            debian|ubuntu)
                echo "  sudo apt install python3"
                ;;
            *)
                echo "  Please install python3 using your package manager."
                ;;
        esac
    else
        echo "  Please install python3."
    fi
    exit 1
fi

PY_MISSING=$("$TANUKI_PYTHON" -c "
import sqlite3
try:
    import lzma
except ImportError:
    print('lzma')
try:
    import gzip
except ImportError:
    print('gzip')
" 2>/dev/null)

if [ -n "$PY_MISSING" ]; then
    echo "Warning: Missing Python built-in modules: $PY_MISSING"
fi

if ! "$TANUKI_PYTHON" -c "import zstandard" 2>/dev/null && \
   ! "$TANUKI_PYTHON" -c "import pyzstd" 2>/dev/null && \
   ! command -v zstd &> /dev/null; then
    echo "Warning: zstd not available (some .deb packages may fail to extract)"
    echo "  Install python3-zstandard via your package manager."
fi

if ! "$TANUKI_PYTHON" -c "import lupa" 2>/dev/null; then
    echo "Warning: Lua config parser disabled (install python3-lupa for full functionality)"
fi

if [[ $# -eq 0 ]] || [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    exec "$TANUKI_PYTHON" -m main -h
else
    exec "$TANUKI_PYTHON" -m main "$@"
fi
