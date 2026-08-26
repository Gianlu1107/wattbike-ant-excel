#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
if [[ $# -eq 0 ]]; then
  exec python -m wattbike_logger gui
fi
exec python -m wattbike_logger "$@"
