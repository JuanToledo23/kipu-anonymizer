#!/usr/bin/env sh
# Starts the anonymization service on $PORT (default 9000).
set -eu
cd "$(dirname "$0")"
PORT="${PORT:-9000}"
if [ -x .venv/bin/uvicorn ]; then
  UVICORN=.venv/bin/uvicorn
else
  UVICORN="uv run uvicorn"
fi
exec $UVICORN kipu.api:app_factory --factory --host 0.0.0.0 --port "$PORT"
