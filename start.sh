#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -f backend/.env && ! -f .env ]]; then
  echo "Crie backend/.env a partir de backend/.env.example"
  exit 1
fi

if [[ ! -d backend/.venv ]]; then
  python3 -m venv backend/.venv
fi
# shellcheck disable=SC1091
source backend/.venv/bin/activate
pip install -q -r backend/requirements.txt

if [[ ! -d frontend/node_modules ]]; then
  (cd frontend && npm install)
fi

if [[ ! -d frontend/build ]]; then
  (cd frontend && CI=true npm run build)
fi

export FRONTEND_BUILD="$ROOT/frontend/build"
cd backend
exec uvicorn server:app --host 0.0.0.0 --port 8000
