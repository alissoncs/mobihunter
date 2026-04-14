#!/usr/bin/env bash
# Servidor da listagem (http://127.0.0.1:9090 por defeito; MOBIHUNTER_UI_PORT, etc.)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

PYTHON=python3
command -v python >/dev/null 2>&1 && PYTHON=python

exec "$PYTHON" -m mobihunter.web "$@"
