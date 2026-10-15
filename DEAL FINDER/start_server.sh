#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
# Override DEAL_FINDER_PYTHON if the environment lives elsewhere.
server_python="${DEAL_FINDER_PYTHON:-$PWD/.venv/bin/python3}"
if [[ ! -x "$server_python" ]]; then
  server_python="/Users/shopify/Desktop/DEAL FINDER/.venv/bin/python3"
fi
if [[ ! -x "$server_python" ]]; then
  print -u2 'Create a Python 3.11+ virtual environment in this folder and install requirements.txt.'
  exit 1
fi
export SSL_CERT_FILE="${SSL_CERT_FILE:-$("$server_python" -m certifi)}"
exec "$server_python" -m uvicorn deal_finder_api:app --host "${DEAL_FINDER_HOST:-127.0.0.1}" --port "${DEAL_FINDER_PORT:-8001}"
