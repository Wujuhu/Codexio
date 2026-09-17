#!/bin/bash
# Sourced by the macOS entry scripts. Keep all dependencies inside the project.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "此脚本需要在 macOS 上运行。" >&2
    exit 1
fi
CODEXIO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CODEXIO_ROOT"

if [[ ! -x .venv/bin/python ]]; then
    CODEXIO_BOOTSTRAP="${CODEXIO_PYTHON:-}"
    if [[ -z "$CODEXIO_BOOTSTRAP" ]]; then
        for candidate in python3.13 python3.12 python3; do
            if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,14)' 2>/dev/null; then
                CODEXIO_BOOTSTRAP="$(command -v "$candidate")"
                break
            fi
        done
    fi
    if [[ -z "$CODEXIO_BOOTSTRAP" ]]; then
        echo "请安装 Python 3.12 或 3.13，或用 CODEXIO_PYTHON 指定解释器路径。" >&2
        exit 1
    fi
    "$CODEXIO_BOOTSTRAP" -m venv .venv
fi
CODEXIO_VENV_PYTHON="$CODEXIO_ROOT/.venv/bin/python"
"$CODEXIO_VENV_PYTHON" -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,14), "macOS 开发环境需要 Python 3.12 或 3.13"'
CODEXIO_DEPS_HASH="$("$CODEXIO_VENV_PYTHON" -c 'import hashlib; from pathlib import Path; print(hashlib.sha256(b"".join(Path(p).read_bytes() for p in ("requirements.txt", "requirements-macos.txt"))).hexdigest())')"
CODEXIO_DEPS_MARKER=".venv/.codexio-macos-dependencies"
CODEXIO_SAVED_HASH=""
if [[ -f "$CODEXIO_DEPS_MARKER" ]]; then
    CODEXIO_SAVED_HASH="$("$CODEXIO_VENV_PYTHON" -c 'from pathlib import Path; print(Path(".venv/.codexio-macos-dependencies").read_text(encoding="utf-8").strip())')"
fi
if [[ "$CODEXIO_DEPS_HASH" != "$CODEXIO_SAVED_HASH" ]]; then
    "$CODEXIO_VENV_PYTHON" -m pip install -r requirements-macos.txt
    printf '%s\n' "$CODEXIO_DEPS_HASH" > "$CODEXIO_DEPS_MARKER"
fi
export PYTHONPATH="$CODEXIO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
