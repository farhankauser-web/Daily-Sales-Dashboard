# Walmart Marketplace → Amazon MCF Automation

Automatically fulfills Walmart orders through Amazon Multi-Channel
Fulfillment: import → SKU mapping → inventory check → MCF create
(Blank Box + Block-AMZL) → status monitoring → tracking upload → audit.

## One-time setup

### 1. Environment variables (`.env`)

```
WALMART_CLIENT_ID=...            # developer.walmart.com → API keys
WALMART_CLIENT_SECRET=...
WALMART_MCF_MARKETPLACE=usa      # which AmazonAPIConfig to fulfill from
WALMART_MCF_BLANK_BOX=Required   # or NotRequired
WALMART_MCF_BLOCK_AMZL=Required  # or NotRequired
WALMART_MCF_ALERT_EMAILS=ops@infinitee.biz
```

### 2. Amazon Seller Central (packaging compliance — NOT API-settable)

* **Unbranded packing slips**: Settings → Fulfillment by Amazon →
  Multi-Channel Fulfillment → Packing slip: remove the Amazon logo, set a
  neutral seller name/message. BLANK_BOX only covers the outer box.
* **Block Amazon Logistics (account default)**: enable in MCF settings as a
  backstop. The API constraint on each order takes precedence, but known
  Amazon bugs occasionally leak AMZL — the pipeline alerts if a `TBA…`
  tracking number appears and never uploads it to Walmart.
* Verify **Blank Box enrollment**: `SPAPIClient.get_mcf_features()` should
  list `BLANK_BOX` with `sellerEligible: true`.

### 3. SKU mappings

Admin → Walmart → Amazon MCF → SKU Mappings. One Walmart SKU → one Amazon
seller SKU; disabled or missing mappings route the order to ERROR + email.

## Order lifecycle

`NEW → VALIDATED → PROCESSING → MCF_CREATED → SHIPPED → TRACKING_UPLOADED
→ COMPLETED`, side states `ERROR` / `HOLD` (inventory) / `CANCELLED`.
Every transition is compare-and-swap atomic and writes an AuditEvent.

## Idempotency guarantees

* Walmart PO id UNIQUE → duplicate imports impossible.
* MCF order id is deterministic: the bare Walmart `customerOrderId`
  (identical to the manual ops convention; PO id as fallback) + UNIQUE.
  Early orders used `WM-{po}` / `WM-{customerOrderId}` prefixes. Because
  the id shape no longer marks an order as automated, "ours vs manual"
  checks go through the AmazonMCFOrder table. On an ambiguous create
  failure the pipeline calls `getFulfillmentOrder` first and adopts the
  existing order instead of re-creating.
* Package `upload_hash` (po|package|carrier|tracking) UNIQUE → the same
  tracking line can never be uploaded to Walmart twice.
* Every cron command runs under an flock job lock — overlapping runs skip.

## Cron (deploy/crontab.txt)

| Command | Cadence |
|---|---|
| `walmart_import_orders` | */5 min |
| `walmart_submit_mcf` | */10 min |
| `walmart_check_status` | */15 min |
| `walmart_upload_tracking` | */15 min (offset +7) |
| `walmart_reconcile` | 02:15 nightly |

## Retry policy

429/5xx/network → exponential backoff (1→30 s, 5 tries) then the order
rolls back to a retryable state for the next cron cycle. 4xx (bad address,
bad SKU, auth) → **no retry**, order → ERROR + admin email. Admin actions:
Reprocess (ERROR/HOLD/CANCELLED → NEW), Retry tracking upload.

## Tests

`python manage.py test apps.walmart_mcf` — 16 tests covering the state
machine, duplicate-import protection, unmapped-SKU routing, inventory
holds, ambiguous-create adoption, transient rollback, tracking dedupe and
the AMZL leak alert.
