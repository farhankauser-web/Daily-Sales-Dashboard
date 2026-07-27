#!/bin/bash
# Infinitee local scheduler — invoked by launchd agents.
# Usage: run_group.sh <every5|every15|hourly|every6h|daily>
cd /Users/farhankauser/Desktop/project/infinitee_app
PY=/opt/anaconda3/envs/infinitee/bin/python
LOG=logs
case "$1" in
  every5)
    $PY manage.py ingest_ams_s3 --marketplace usa >> $LOG/ams.log 2>&1
    ;;
  every15)
    $PY manage.py sync_today_ppc --marketplace usa >> $LOG/ppc_today.log 2>&1
    ;;
  hourly)
    $PY manage.py snapshot_hourly_metrics >> $LOG/hourly.log 2>&1
    $PY manage.py atlas_alerts            >> $LOG/atlas.log 2>&1
    # Walmart: import new orders from Walmart + auto-create Amazon MCF orders
    $PY manage.py walmart_import_orders   >> $LOG/walmart_mcf.log 2>&1
    $PY manage.py walmart_submit_mcf      >> $LOG/walmart_mcf.log 2>&1
    ;;
  every6h)
    # Walmart: pull Amazon shipment status + push new tracking numbers to Walmart
    $PY manage.py walmart_check_status         >> $LOG/walmart_mcf.log 2>&1
    $PY manage.py walmart_sync_cancellations   >> $LOG/walmart_mcf.log 2>&1
    $PY manage.py walmart_upload_tracking      >> $LOG/walmart_mcf.log 2>&1
    ;;
  daily)
    $PY manage.py sync_daily_metrics      >> $LOG/daily.log 2>&1
    for R in usa uk ae sa; do
      $PY manage.py sync_planning_inventory --region $R >> $LOG/inventory_planning.log 2>&1
    done
    $PY manage.py walmart_reconcile       >> $LOG/walmart_mcf.log 2>&1
    # link manual-MCF tracking to Active orders + reflow premature-COMPLETED
    $PY manage.py walmart_reconcile_manual >> $LOG/walmart_mcf.log 2>&1
    $PY manage.py walmart_sync_inventory  >> $LOG/walmart_mcf.log 2>&1
    $PY manage.py walmart_backfill_tracking >> $LOG/walmart_mcf.log 2>&1
    $PY manage.py ingest_ads_detail_reports --rewind 2 >> $LOG/ads_detail.log 2>&1
    $PY manage.py compute_campaign_profit --rewind 2   >> $LOG/campaign_profit.log 2>&1
    ;;
esac
