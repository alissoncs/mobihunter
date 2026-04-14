#!/usr/bin/env bash
# Corre todos os importadores configurados, em sequência.
# Única opção: --agency <slug> para correr só um (foxter, guarida, creditoreal).
# URLs: cada script Python lê apenas config/urls.json (sem argumentos).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

PYTHON=python3
command -v python >/dev/null 2>&1 && PYTHON=python

IMPORTERS_SPEC=(
  "foxter:scripts/importers/foxter.py"
  "guarida:scripts/importers/guarida.py"
  "creditoreal:scripts/importers/creditoreal.py"
)

FILTER_AGENCY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agency)
      if [[ -z "${2:-}" ]]; then
        echo "run-import-all.sh: --agency requer um valor (ex.: foxter, guarida ou creditoreal)" >&2
        exit 1
      fi
      FILTER_AGENCY="$2"
      shift 2
      ;;
    *)
      echo "run-import-all.sh: argumento desconhecido: $1 (use apenas --agency opcional)" >&2
      exit 1
      ;;
  esac
done

_lc() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

if [[ -n "$FILTER_AGENCY" ]]; then
  found=0
  for spec in "${IMPORTERS_SPEC[@]}"; do
    ag="${spec%%:*}"
    if [[ "$(_lc "$FILTER_AGENCY")" == "$(_lc "$ag")" ]]; then
      found=1
      break
    fi
  done
  if [[ "$found" -eq 0 ]]; then
    echo "run-import-all.sh: agency desconhecida: $FILTER_AGENCY" >&2
    echo "Válidas: foxter, guarida, creditoreal" >&2
    exit 1
  fi
fi

for spec in "${IMPORTERS_SPEC[@]}"; do
  ag="${spec%%:*}"
  rel="${spec#*:}"
  if [[ -n "$FILTER_AGENCY" ]] && [[ "$(_lc "$FILTER_AGENCY")" != "$(_lc "$ag")" ]]; then
    continue
  fi
  script="$ROOT/$rel"
  if [[ ! -f "$script" ]]; then
    echo "run-import-all.sh: em falta: $rel" >&2
    exit 1
  fi
  echo ">>> $rel" >&2
  "$PYTHON" "$script"
done
