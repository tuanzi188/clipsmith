#!/usr/bin/env bash
# Start backend + frontend together (Git Bash / macOS / Linux).
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -x "$root/backend/.venv/Scripts/python.exe" ]]; then
  py="$root/backend/.venv/Scripts/python.exe"
elif [[ -x "$root/backend/.venv/bin/python" ]]; then
  py="$root/backend/.venv/bin/python"
else
  echo "backend/.venv not found; run: cd backend && python -m venv .venv && pip install -e ."
  exit 1
fi

( cd "$root/backend" && "$py" -m uvicorn clipsmith.api:app --port 8000 --reload ) &
backend_pid=$!
trap 'kill $backend_pid 2>/dev/null || true' EXIT

cd "$root/frontend"
npm run dev
