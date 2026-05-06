#!/usr/bin/env bash
set -euo pipefail

# Always run Django with the correct conda env, no manual activation needed.
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "Using conda env: infinitee"
conda run -n infinitee python -m pip install -r requirements.txt
conda run -n infinitee python manage.py migrate
exec conda run -n infinitee python manage.py runserver
