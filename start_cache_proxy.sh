#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

: "${DIFY_API_KEY:?DIFY_API_KEY must be configured in .env or the environment}"

pkill -f "[p]ython3 cache_proxy.py" || true
nohup python3 cache_proxy.py > cache_proxy.log 2>&1 &

sleep 2
ps -ef | grep "python3 cache_proxy.py" | grep -v grep || true
