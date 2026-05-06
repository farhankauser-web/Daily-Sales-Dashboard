# Infinitee Xclusives — Operations Intelligence Platform
## Django 5 · MySQL 8 · Amazon SP-API · Claude AI · RBAC

## Quick Start — Docker (Fastest)
```bash
echo "ANTHROPIC_API_KEY=sk-ant-api03-your-key" > .env
docker compose up -d
# Visit http://localhost:8000
# Login: admin@infiniteexclusives.com / ChangeMe2024!
```

## Quick Start — Local Python
```bash
# MySQL
mysql -u root -p -e "CREATE DATABASE infinitee_db CHARACTER SET utf8mb4; CREATE USER 'infinitee_user'@'localhost' IDENTIFIED BY 'pass'; GRANT ALL ON infinitee_db.* TO 'infinitee_user'@'localhost';"

# Python env
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Config: copy .env.example → .env, fill values
# Generate Fernet key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Setup
python manage.py makemigrations users amazon_api dashboard core
python manage.py migrate && python manage.py createcachetable
python manage.py setup_infinitee --email admin@co.com --password StrongPass123!
python manage.py seed_demo_data --days 90
python manage.py seed_inventory_ppc --days 60
python manage.py calculate_margins
python manage.py runserver
```

## Pages & Permissions
| Page | URL | Permission |
|---|---|---|
| Daily Dashboard | /dashboard/ | can_view_dashboard |
| Historical | /dashboard/historical/ | can_view_historical |
| PPC Analytics | /dashboard/ppc/ | can_view_ppc |
| Inventory | /dashboard/inventory/ | can_view_inventory |
| COGS Upload | /dashboard/cogs/ | can_manage_cogs |
| Monthly Targets | /dashboard/targets/ | can_manage_targets |
| Product Catalog | /dashboard/catalog/ | can_manage_catalog |
| AI Summary | /dashboard/summary/ | can_generate_ai_summary |
| Alerts | /dashboard/alerts/ | all |
| API Config | /api-config/ | can_configure_api |
| User Management | /auth/manage/ | can_manage_users |
| Roles | /auth/roles/ | can_manage_users |
| Audit Log | /auth/audit/ | can_view_audit_log |

## Models (MySQL tables)
ix_users, ix_roles, ix_audit_log, ix_amazon_api_config, ix_anthropic_config,
ix_api_sync_log, ix_products, ix_cogs, ix_monthly_targets, ix_daily_metrics,
ix_inventory_snapshots, ix_ppc_snapshots, ix_alerts

## Production
See deploy/nginx.conf, deploy/infinitee.service, deploy/deploy.sh
