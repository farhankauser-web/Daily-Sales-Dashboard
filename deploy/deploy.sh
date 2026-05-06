#!/bin/bash
# deploy/deploy.sh — Full deployment script for Infinitee Xclusives
# Usage: ./deploy/deploy.sh [first-run|update]
set -e

MODE=${1:-update}
APP_DIR="/path/to/infinitee_app"
VENV_DIR="$APP_DIR/venv"
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
MANAGE="$PYTHON $APP_DIR/manage.py"

echo "============================================================"
echo "  Infinitee Xclusives — Deploy ($MODE)"
echo "============================================================"

cd "$APP_DIR"

# ── Install / update dependencies ────────────────────────────────────────────
echo "[1/6] Installing Python dependencies…"
$PIP install -q -r requirements.txt

# ── Database migrations ───────────────────────────────────────────────────────
echo "[2/6] Running migrations…"
$MANAGE migrate --noinput

# ── Collect static files ──────────────────────────────────────────────────────
echo "[3/6] Collecting static files…"
$MANAGE collectstatic --noinput --clear

# ── Cache table ───────────────────────────────────────────────────────────────
echo "[4/6] Ensuring cache table…"
$MANAGE createcachetable || true

# ── First-run setup ───────────────────────────────────────────────────────────
if [ "$MODE" = "first-run" ]; then
    echo "[5/6] Running first-time setup…"
    $MANAGE setup_infinitee \
        --email  "${ADMIN_EMAIL:-admin@infiniteexclusives.com}" \
        --name   "${ADMIN_NAME:-Farhan Kauser}" \
        --password "${ADMIN_PASSWORD:-ChangeMe2024!}"

    echo "      Seeding demo data (90 days)…"
    $MANAGE seed_demo_data --days 90
    $MANAGE seed_inventory_ppc --days 60
    $MANAGE calculate_margins
else
    echo "[5/6] Skipping first-run setup (update mode)"
fi

# ── Restart application ───────────────────────────────────────────────────────
echo "[6/6] Restarting Gunicorn…"
sudo systemctl restart infinitee || true

echo ""
echo "✅ Deployment complete!"
echo "   App: https://yourdomain.com"
echo "   Admin: https://yourdomain.com/admin/"
echo "   Logs: $APP_DIR/logs/"
