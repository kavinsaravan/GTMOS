#!/bin/bash
# Starts the LangGraph worker (Planner -> Researcher -> Reviewer) as one local process.
# Needs: a venv with `pip install -r agents/requirements.txt` and the GTMOS API token
# (GTMOS_API_TOKEN, or .data/api_token).
# Usage: GTMOS_API_URL=https://<api-id>.execute-api.us-west-2.amazonaws.com/prod agents/start.sh [python]
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${1:-python}"
cd "$ROOT"   # `python -m` puts the cwd on sys.path; never inherit a surprising one
export PYTHONPATH="$ROOT"
: "${GTMOS_API_URL:?set GTMOS_API_URL to the deployed API base URL}"
# python.org builds on macOS ship without a CA bundle; point TLS at certifi's (verification stays on).
export SSL_CERT_FILE="${SSL_CERT_FILE:-$("$PY" -c 'import certifi; print(certifi.where())')}"
echo "worker running; polling for queued runs (Ctrl-C to stop)"
exec "$PY" -m agents.worker
