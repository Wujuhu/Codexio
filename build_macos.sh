#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/scripts/macos_env.sh"
exec "$CODEXIO_VENV_PYTHON" scripts/build_macos.py "$@"
