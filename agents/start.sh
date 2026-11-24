#!/bin/bash
# Starts the three Band agents as separate local processes. Ctrl-C stops them all.
# Needs: a venv with `pip install -r agents/requirements.txt`, AWS profile `gtmos` (or BAND_* env vars).
# Usage: GTMOS_API_URL=https://<api-id>.execute-api.us-west-2.amazonaws.com/prod agents/start.sh [python]
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${1:-python}"
cd "$ROOT"   # `python -m` puts the cwd on sys.path; never inherit a surprising one
export PYTHONPATH="$ROOT"
: "${GTMOS_API_URL:?set GTMOS_API_URL to the deployed API base URL}"
# python.org builds on macOS ship without a CA bundle; point TLS at certifi's (verification stays on).
export SSL_CERT_FILE="${SSL_CERT_FILE:-$("$PY" -c 'import certifi; print(certifi.where())')}"
trap 'kill 0' EXIT
for role in researcher reviewer planner; do
  "$PY" -m "agents.$role" > "/tmp/gtmos_agent_$role.log" 2>&1 &
done
echo "agents running; logs in /tmp/gtmos_agent_*.log"
wait
