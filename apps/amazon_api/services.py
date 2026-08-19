"""
P0.1 — Data Kiosk client methods for SPAPIClient

Add these methods to the SPAPIClient class in apps/amazon_api/services.py

Architecture (Section H):
- Reuses SPAPIClient auth/endpoint
- Query cost is low (~25s for a month of one marketplace)
- Daily incremental ingest with periodic backfill is comfortably affordable
- No new auth mechanism — uses existing LWATokenManager
"""


def get_fba_economics(self, start_date, end_date, marketplace_id, fee_types=None, poll_timeout=300):
    """
    Fetch FBA economics (component-level fee breakdown) from Data Kiosk.

    Amazon Data Kiosk — POST /dataKiosk/2023-11-15/queries with a GraphQL query
    for the `analytics_economics_2024_03_15` schema.

    Args:
        start_date:     date object (or YYYY-MM-DD string)
        end_date:       date object (or YYYY-MM-DD string)
        marketplace_id: Amazon marketplace ID (e.g. ATVPDKIKX0DER for USA)
        fee_types:      list of fee type enums to include components for.
                        Default: ['FBA_FULFILLMENT_FEE'].
                        Valid: FBA_FULFILLMENT_FEE, FBA_STORAGE_FEE.
        poll_timeout:   max seconds to wait for query completion (default 300 = 5 min)

    Returns:
        {
            'queryId': str,
            'processingStatus': 'DONE',
            'records': [  # JSONL rows, one per (date × MSKU)
                {
                    'date': 'YYYY-MM-DD',
                    'productId': 'SELLER-SKU',
                    'quantity': 2242.0,
                    'amount': 16790.28,  # gross rate-card amount
                    'amountPerUnit': 7.48,  # final charge (after promo + tax)
                    'promotionAmount': 10.50,
                    'taxAmount': 0.00,
                    'currencyCode': 'USD',
                    'feeType': 'FBA_FULFILLMENT_FEE',
                    'components': [
                        {
                            'name': 'BaseFbaFulfilmentFee',
                            'quantity': 2242.0,
                            'amount': 16720.00,
                            'amountPerUnit': 7.46,
                            'promotionAmount': 0.00,
                            'taxAmount': 0.00,
                        },
                        {
                            'name': 'FuelSurcharge',
                            'quantity': 2242.0,
                            'amount': 70.28,
                            'amountPerUnit': 0.031,
                            ...
                        },
                        ...
                    ],
                },
                ...
            ],
        }

    Raises:
        RuntimeError on API error, timeout, or malformed response.

    Notes:
        • `includeComponentsForFeeTypes` is MANDATORY in the query.
          Omit it and Fee.components is null. (Section A)
        • Component .name is free text, not an enum. The ingest layer must
          handle the observed taxonomy: BaseFbaFulfilmentFee (note: single-l
          Fulfilment, not Fulfillment as in settlement), FuelSurcharge,
          LowInventoryLevelFee. (Section B)
        • quantity and amountPerUnit are Float (nullable when not per-unit).
          (Section H, note 6)
        • Marketplace/currency is per-row, never assume USD. (Section H, note 7)
    """
    import time
    from datetime import date as date_type

    # Normalize dates
    if isinstance(start_date, date_type):
        start_date = start_date.isoformat()
    if isinstance(end_date, date_type):
        end_date = end_date.isoformat()

    fee_types = fee_types or ['FBA_FULFILLMENT_FEE']
    if not isinstance(fee_types, list):
        fee_types = [fee_types]

    # ── Construct GraphQL query (Section A) ──────────────────────────────
    # The schema is analytics_economics_2024_03_15; root is Query.economics
    graphql_query = f"""
    query {{
      analytics_economics_2024_03_15 {{
        economics(
          startDate: "{start_date}"
          endDate: "{end_date}"
          aggregateBy: {{ date: DAY, productId: MSKU }}
          marketplaceIds: ["{marketplace_id}"]
          includeComponentsForFeeTypes: [{', '.join(f'"{ft}"' for ft in fee_types)}]
        ) {{
          date
          productId
          quantity
          amount
          amountPerUnit
          promotionAmount
          taxAmount
          currencyCode
          feeType
          components {{
            name
            quantity
            amount
            amountPerUnit
            promotionAmount
            taxAmount
          }}
        }}
      }}
    }}
    """

    # ── Submit query to Data Kiosk (Section A) ──────────────────────────
    headers = self._headers()
    submit_resp = self._post(
        '/dataKiosk/2023-11-15/queries',
        json_body={'query': graphql_query},
        timeout=30,
    )

    query_id = submit_resp.get('queryId')
    if not query_id:
        raise RuntimeError(
            f'Data Kiosk submit failed: no queryId in response: {submit_resp}'
        )

    # ── Poll for completion (Section A: ~25s typical for 1 month) ──────────
    elapsed = 0
    while elapsed < poll_timeout:
        time.sleep(2)
        elapsed += 2

        status_resp = self._get(
            f'/dataKiosk/2023-11-15/queries/{query_id}',
            timeout=30,
        )

        processing_status = status_resp.get('processingStatus')
        if processing_status == 'DONE':
            # Download the JSONL result
            download_url = status_resp.get('downloadUrl')
            if not download_url:
                raise RuntimeError(
                    f'Query {query_id} marked DONE but no downloadUrl provided'
                )

            # Fetch the gzip-compressed JSONL from S3
            import gzip
            import requests as req
            dl_resp = req.get(download_url, timeout=60)
            dl_resp.raise_for_status()

            # Decompress and parse JSONL
            raw_jsonl = gzip.decompress(dl_resp.content).decode('utf-8')
            records = [
                __import__('json').loads(line)
                for line in raw_jsonl.strip().split('\n')
                if line.strip()
            ]

            return {
                'queryId': query_id,
                'processingStatus': 'DONE',
                'records': records,
            }

        if processing_status in ('FAILED', 'CANCELLED'):
            raise RuntimeError(
                f'Data Kiosk query {query_id} status: {processing_status}\n'
                f'Error: {status_resp.get("statusMessage", "no details")}'
            )

        # Still IN_QUEUE or IN_PROGRESS — continue polling

    # Timeout — return pending result with queryId so caller can resume
    raise RuntimeError(
        f'Data Kiosk query {query_id} did not complete within {poll_timeout}s'
    )
