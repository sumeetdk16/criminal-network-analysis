#!/usr/bin/env bash
# One-command setup and launch. Safe to run again any time - each step is
# skipped if it's already done.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

source venv/bin/activate

echo "Installing dependencies..."
pip install -q -r backend/requirements.txt

if [ ! -f data/raw/firs.json ]; then
  echo "Generating synthetic case data..."
  python3 data/generator.py
fi

cd backend
echo
echo "Starting server - open http://127.0.0.1:8000"
python3 run.py
