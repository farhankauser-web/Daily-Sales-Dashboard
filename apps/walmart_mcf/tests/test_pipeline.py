"""
Walmart → MCF pipeline tests. All external APIs are mocked; every test
asserts an idempotency or state-machine guarantee from the spec.
"""
from __future__ import annotations

from datetime import datetime, timezone as tz
from unittest import mock

from django.test import TestCase, override_settings

from apps.walmart_mcf import pipeline
from apps.walmart_mcf.models import (AmazonMCFOrder, ShipmentPackage,
                                     SkuMapping, WalmartOrder,
                                     WalmartOrderItem, WalmartOrderState as S)
from apps.walmart_mcf.state import IllegalTransition, transition


def make_order(po='WMPO-1001', status=S.NEW, sku='WM-TWL-1', qty=2):
    order = WalmartOrder.objects.create(
        purchase_order_id=po, status=status,
        order_date=datetime(2026, 7, 1, tzinfo=tz.utc),
        customer_name='Jane Buyer', phone='555-0100',
        shipping_address={'name': 'Jane Buyer', 'address1': '1 Main St',
                          'city': 'Austin', 'state': 'TX',
                          'postalCode': '78701', 'country': 'US'},
        shipping_method='Standard')
    WalmartOrderItem.objects.create(order=order, line_number='1',
                                    walmart_sku=sku, quantity=qty)
    return order


RAW_WALMART_ORDER = {
    'purchaseOrderId': 'WMPO-2001',
    'customerOrderId': 'C-9',
    'orderDate': 1751328000000,
    'shippingInfo': {
        'phone': '555-0101', 'methodCode': 'Standard',
        'postalAddress': {'name': 'Bob', 'address1': '2 Oak Ave',
                          'city': 'Dallas', 'state': 'TX',
                          'postalCode': '75201', 'country': 'US'},
    },
    'orderLines': {'orderLine': [{
        'lineNumber': '1',
        'item': {'sku': 'WM-TWL-1', 'productName': 'Bath Towel 4pk'},
        'orderLineQuantity': {'amount': '2'},
        'charges': {'charge': [{'chargeType': 'PRODUCT',
                                'chargeAmount': {'amount': 39.99}}]},
    }]},
}


class StateMachineTests(TestCase):
    def test_happy_path_transitions(self):
        order = make_order()
        for nxt in [S.VALIDATED, S.PROCESSING, S.MCF_CREATED, S.SHIPPED,
                    S.TRACKING_UPLOADED, S.COMPLETED]:
            self.assertTrue(transition(order, nxt, 'test'))
        self.assertEqual(order.audit_events.count(), 6)

    def test_illegal_transition_refused(self):
        order = make_order()
        with self.assertRaises(IllegalTransition):
            transition(order, S.SHIPPED, 'test')   # NEW → SHIPPED not allowed

    def test_cas_only_one_winner(self):
        """Two workers holding the same stale row: exactly one wins."""
        order = make_order()
        stale_copy = WalmartOrder.objects.get(pk=order.pk)
        self.assertTrue(transition(order, S.VALIDATED, 'worker-a'))
        self.assertFalse(transition(stale_copy, S.VALIDATED, 'worker-b'))

    def test_completed_is_terminal(self):
        order = make_order(status=S.COMPLETED)
        with self.assertRaises(IllegalTransition):
            transition(order, S.NEW, 'test')


class ImportTests(TestCase):
    def _wc(self, MockWC, released=None, all_orders=None):
        wc = MockWC.return_value
        wc.get_released_orders.return_value = released or []
        wc.get_all_orders.return_value = all_orders or []
        wc.acknowledge.return_value = {}
        return wc

    @mock.patch.object(pipeline, 'WalmartClient')
    def test_import_and_duplicate_protection(self, MockWC):
        wc = self._wc(MockWC, released=[RAW_WALMART_ORDER])

        res1 = pipeline.import_orders()
        self.assertEqual(res1['imported'], 1)
        self.assertEqual(res1['acknowledged'], 1)

        res2 = pipeline.import_orders()          # same payload again
        self.assertEqual(res2['imported'], 0)
        self.assertEqual(res2['skipped_existing'], 1)
        self.assertEqual(WalmartOrder.objects.count(), 1)

        order = WalmartOrder.objects.get()
        self.assertEqual(order.purchase_order_id, 'WMPO-2001')
        self.assertEqual(order.items.get().quantity, 2)
        self.assertIsNotNone(order.acknowledged_at)

    @mock.patch.object(pipeline, 'WalmartClient')
    def test_acknowledged_unshipped_imported_without_reack(self, MockWC):
        """Orders acked by another system still get imported — but no ack call."""
        import copy
        raw = copy.deepcopy(RAW_WALMART_ORDER)
        raw['orderLines']['orderLine'][0]['orderLineStatuses'] = {
            'orderLineStatus': [{'status': 'Acknowledged',
                                 'statusQuantity': {'amount': '2'}}]}
        wc = self._wc(MockWC, all_orders=[raw])
        res = pipeline.import_orders()
        self.assertEqual(res['imported'], 1)
        self.assertEqual(res['acknowledged'], 0)
        wc.acknowledge.assert_not_called()
        self.assertIsNotNone(WalmartOrder.objects.get().acknowledged_at)

    @mock.patch.object(pipeline, 'WalmartClient')
    def test_fully_shipped_orders_not_imported(self, MockWC):
        import copy
        raw = copy.deepcopy(RAW_WALMART_ORDER)
        raw['orderLines']['orderLine'][0]['orderLineStatuses'] = {
            'orderLineStatus': [{'status': 'Shipped',
                                 'statusQuantity': {'amount': '2'}}]}
        self._wc(MockWC, all_orders=[raw])
        res = pipeline.import_orders()
        self.assertEqual(res['imported'], 0)
        self.assertEqual(res['already_fulfilled'], 1)
        self.assertEqual(WalmartOrder.objects.count(), 0)


@override_settings(WALMART_MCF_FEATURES={'BLANK_BOX': 'Required',
                                         'BLOCK_AMZL': 'Required'})
class SubmitTests(TestCase):
    def _client(self, sellable=10, create_raises=None, fo_exists=True):
        client = mock.Mock()
        client.get_mcf_feature_sku.return_value = {
            'isEligible': True, 'skuCount': str(sellable)}
        if create_raises:
            client.create_mcf_order.side_effect = create_raises
        else:
            client.create_mcf_order.return_value = {}
        fo_payload = {'fulfillmentOrder': {
            'fulfillmentOrderStatus': 'Received',
            'featureConstraints': [
                {'featureName': 'BLANK_BOX',
                 'featureFulfillmentPolicy': 'Required'},
                {'featureName': 'BLOCK_AMZL',
                 'featureFulfillmentPolicy': 'Required'}],
        }, 'fulfillmentShipments': []}
        client._get.return_value = {'payload': fo_payload} if fo_exists \
            else mock.Mock()
        if not fo_exists:
            import requests
            resp = mock.Mock(status_code=404)
            client._get.side_effect = requests.HTTPError(response=resp)
        return client

    def test_unmapped_sku_goes_to_error_no_amazon_call(self):
        order = make_order()
        client = self._client()
        with mock.patch.object(pipeline, 'notify_admin') as note:
            self.assertEqual(pipeline._submit_one(order, client), 'error')
        order.refresh_from_db()
        self.assertEqual(order.status, S.ERROR)
        self.assertIn('WM-TWL-1', order.error_reason)
        client.create_mcf_order.assert_not_called()
        note.assert_called_once()

    def test_disabled_mapping_treated_as_unmapped(self):
        order = make_order()
        SkuMapping.objects.create(walmart_sku='WM-TWL-1',
                                  amazon_sku='TWL-1', enabled=False)
        self.assertEqual(pipeline._submit_one(order, self._client()), 'error')

    def test_insufficient_inventory_holds_order(self):
        order = make_order(qty=5)
        SkuMapping.objects.create(walmart_sku='WM-TWL-1', amazon_sku='TWL-1')
        client = self._client(sellable=1)
        with mock.patch.object(pipeline, 'notify_admin'):
            self.assertEqual(pipeline._submit_one(order, client), 'hold')
        order.refresh_from_db()
        self.assertEqual(order.status, S.HOLD)
        client.create_mcf_order.assert_not_called()

    def test_successful_submit_creates_single_mcf_order(self):
        order = make_order()
        SkuMapping.objects.create(walmart_sku='WM-TWL-1', amazon_sku='TWL-1')
        client = self._client()
        self.assertEqual(pipeline._submit_one(order, client), 'submitted')
        order.refresh_from_db()
        self.assertEqual(order.status, S.MCF_CREATED)
        mcf = order.mcf
        # no customer_order_id on this fixture → bare PO fallback, no prefix
        self.assertEqual(mcf.fulfillment_order_id, 'WMPO-1001')
        # feature constraints were sent
        kwargs = client.create_mcf_order.call_args.kwargs
        names = {c['featureName'] for c in kwargs['feature_constraints']}
        self.assertEqual(names, {'BLANK_BOX', 'BLOCK_AMZL'})

    def test_ambiguous_timeout_adopts_existing_order_no_duplicate(self):
        """Create times out, but Amazon actually created it → adopt, don't retry-create."""
        import requests
        order = make_order()
        SkuMapping.objects.create(walmart_sku='WM-TWL-1', amazon_sku='TWL-1')
        client = self._client(create_raises=requests.Timeout('boom'),
                              fo_exists=True)
        self.assertEqual(pipeline._submit_one(order, client), 'submitted')
        order.refresh_from_db()
        self.assertEqual(order.status, S.MCF_CREATED)
        self.assertEqual(AmazonMCFOrder.objects.count(), 1)

    def test_transient_failure_rolls_back_to_new(self):
        import requests
        order = make_order()
        SkuMapping.objects.create(walmart_sku='WM-TWL-1', amazon_sku='TWL-1')
        client = self._client(create_raises=requests.Timeout('boom'),
                              fo_exists=False)
        with self.assertRaises(requests.Timeout):
            pipeline._submit_one(order, client)
        order.refresh_from_db()
        self.assertEqual(order.status, S.NEW)      # safe to retry next cycle
        self.assertEqual(AmazonMCFOrder.objects.count(), 0)

    def test_fo_id_uses_customer_order_id_when_present(self):
        """New convention: WM-{customerOrderId}; PO only as fallback."""
        order = make_order()
        order.customer_order_id = '200015999888777'
        order.save(update_fields=['customer_order_id'])
        SkuMapping.objects.create(walmart_sku='WM-TWL-1', amazon_sku='TWL-1')
        client = self._client()
        self.assertEqual(pipeline._submit_one(order, client), 'submitted')
        order.refresh_from_db()
        self.assertEqual(order.mcf.fulfillment_order_id, '200015999888777')
        kwargs = client.create_mcf_order.call_args.kwargs
        self.assertEqual(kwargs['displayable_order_id'], '200015999888777')

    def test_manual_mcf_order_blocks_resubmission(self):
        """A manually created MCF order for the same PO → ERROR, no create."""
        from apps.dashboard.models import McfOrder as DashMcf
        order = make_order()
        SkuMapping.objects.create(walmart_sku='WM-TWL-1', amazon_sku='TWL-1')
        DashMcf.objects.create(marketplace='usa',
                               seller_order_id='MANUAL-WMPO-1001',
                               displayable_order_id='WMPO-1001',
                               status='Complete')
        client = self._client()
        with mock.patch.object(pipeline, 'notify_admin') as note:
            self.assertEqual(pipeline._submit_one(order, client), 'error')
        order.refresh_from_db()
        self.assertEqual(order.status, S.ERROR)
        self.assertIn('MANUAL-WMPO-1001', order.error_reason)
        client.create_mcf_order.assert_not_called()
        note.assert_called_once()

    def test_already_submitted_order_is_skipped(self):
        """CAS prevents double-submit if another worker claimed the order."""
        order = make_order(status=S.PROCESSING)
        SkuMapping.objects.create(walmart_sku='WM-TWL-1', amazon_sku='TWL-1')
        # order is not in NEW/HOLD → transition to VALIDATED loses the race
        self.assertEqual(pipeline._submit_one(order, self._client()),
                         'skipped')


class TrackingTests(TestCase):
    def _shipped_order(self):
        order = make_order(status=S.SHIPPED)
        SkuMapping.objects.create(walmart_sku='WM-TWL-1', amazon_sku='TWL-1')
        mcf = AmazonMCFOrder.objects.create(
            order=order, fulfillment_order_id='WM-WMPO-1001',
            amazon_status='COMPLETE')
        pkg = ShipmentPackage.objects.create(
            mcf_order=mcf, package_number=1, carrier_code='UPS',
            carrier_walmart='UPS', tracking_number='1Z999',
            ship_date=datetime(2026, 7, 2, tzinfo=tz.utc),
            items=[{'sellerSku': 'TWL-1', 'quantity': 2, 'packageNumber': 1}],
            upload_hash='hash-1')
        return order, mcf, pkg

    @mock.patch.object(pipeline, 'WalmartClient')
    def test_upload_then_never_reupload(self, MockWC):
        wc = MockWC.return_value
        wc.update_shipping.return_value = {}
        order, mcf, pkg = self._shipped_order()

        res = pipeline.upload_tracking()
        self.assertEqual(res['uploaded_packages'], 1)
        order.refresh_from_db()
        self.assertEqual(order.status, S.COMPLETED)  # amazon COMPLETE → closed
        self.assertEqual(wc.update_shipping.call_count, 1)
        lines = wc.update_shipping.call_args.args[1]
        self.assertEqual(lines[0]['tracking_number'], '1Z999')
        self.assertEqual(lines[0]['carrier'], 'UPS')

        # Second run: nothing new → no Walmart call
        pipeline.upload_tracking()
        self.assertEqual(wc.update_shipping.call_count, 1)

    @mock.patch.object(pipeline, 'WalmartClient')
    def test_tba_package_never_uploaded(self, MockWC):
        wc = MockWC.return_value
        order, mcf, pkg = self._shipped_order()
        pkg.tracking_number = 'TBA123456789'
        pkg.carrier_code = 'AMZL'
        pkg.save()
        pipeline.upload_tracking()
        wc.update_shipping.assert_not_called()

    def test_duplicate_package_hash_rejected_at_db_level(self):
        order, mcf, pkg = self._shipped_order()
        from django.db import IntegrityError, transaction as tx
        with self.assertRaises(IntegrityError):
            with tx.atomic():
                ShipmentPackage.objects.create(
                    mcf_order=mcf, package_number=1, carrier_code='UPS',
                    tracking_number='1Z999', upload_hash='hash-1')

    def test_harvest_packages_dedupes_and_alerts_on_amzl(self):
        order, mcf, _ = self._shipped_order()
        fo = {'fulfillmentShipments': [{
            'amazonShipmentId': 'SHP1',
            'shippingDate': '2026-07-02T10:00:00Z',
            'fulfillmentShipmentItem': [
                {'sellerSku': 'TWL-1', 'quantity': 2, 'packageNumber': 2}],
            'fulfillmentShipmentPackage': [
                {'packageNumber': 2, 'carrierCode': 'AMZL',
                 'trackingNumber': 'TBA000111222'},
            ],
        }]}
        with mock.patch.object(pipeline, 'notify_admin') as note:
            created = pipeline._harvest_packages(mcf, fo)
            self.assertEqual(created, 1)
            note.assert_called_once()               # AMZL leak alert
            self.assertEqual(pipeline._harvest_packages(mcf, fo), 0)  # dedupe


# ── Archiving / cancellation correctness (multi-SKU partials, Walmart cancels) ──

def _walmart_order_payload(line_statuses):
    """Build a get_order() response with one orderLine per status list."""
    return {'order': {'orderLines': {'orderLine': [
        {'lineNumber': str(i + 1),
         'orderLineStatuses': {'orderLineStatus': [{'status': s}]}}
        for i, s in enumerate(line_statuses)]}}}


class PartialShipmentArchivingTests(TestCase):
    """Issue #1: a multi-SKU order Amazon has only partially shipped must stay
    SHIPPED (Active) — never advance to a terminal/archivable state."""

    def _partial_order(self):
        order = make_order(status=S.SHIPPED, sku='WM-A', qty=1)
        WalmartOrderItem.objects.create(order=order, line_number='2',
                                        walmart_sku='WM-B', quantity=1)
        SkuMapping.objects.create(walmart_sku='WM-A', amazon_sku='A')
        SkuMapping.objects.create(walmart_sku='WM-B', amazon_sku='B')
        mcf = AmazonMCFOrder.objects.create(
            order=order, fulfillment_order_id='WM-WMPO-1001',
            amazon_status='PROCESSING')            # still shipping
        ShipmentPackage.objects.create(            # only SKU A shipped so far
            mcf_order=mcf, package_number=1, carrier_code='UPS',
            carrier_walmart='UPS', tracking_number='1ZA',
            ship_date=datetime(2026, 7, 2, tzinfo=tz.utc),
            items=[{'sellerSku': 'A', 'quantity': 1}], upload_hash='h-a')
        return order, mcf

    def test_partial_not_fully_shipped(self):
        order, _ = self._partial_order()
        self.assertFalse(pipeline._order_fully_shipped(order))

    @mock.patch.object(pipeline, 'WalmartClient')
    def test_partial_stays_shipped_after_upload(self, MockWC):
        wc = MockWC.return_value
        wc.update_shipping.return_value = {}
        # Walmart shows line A shipped, line B still created (not all shipped)
        wc.get_order.return_value = _walmart_order_payload(['Shipped', 'Created'])
        order, _ = self._partial_order()
        pipeline.upload_tracking()
        order.refresh_from_db()
        self.assertEqual(order.status, S.SHIPPED)   # NOT archived

    @mock.patch.object(pipeline, 'WalmartClient')
    def test_second_sku_ships_then_completes(self, MockWC):
        wc = MockWC.return_value
        wc.update_shipping.return_value = {}
        wc.get_order.return_value = _walmart_order_payload(['Shipped', 'Created'])
        order, mcf = self._partial_order()
        pipeline.upload_tracking()                  # SKU A only → stays SHIPPED
        # SKU B now ships and Amazon marks the order COMPLETE
        ShipmentPackage.objects.create(
            mcf_order=mcf, package_number=2, carrier_code='UPS',
            carrier_walmart='UPS', tracking_number='1ZB',
            ship_date=datetime(2026, 7, 3, tzinfo=tz.utc),
            items=[{'sellerSku': 'B', 'quantity': 1}], upload_hash='h-b')
        mcf.amazon_status = 'COMPLETE'
        mcf.save(update_fields=['amazon_status'])
        pipeline.upload_tracking()
        order.refresh_from_db()
        self.assertEqual(order.status, S.COMPLETED)  # now fully shipped → closed


class WalmartCancellationTests(TestCase):
    """Issues #2 & #3: Walmart-side cancellations are detected and archived."""

    @mock.patch.object(pipeline, 'WalmartClient')
    def test_sync_cancels_pre_mcf_order(self, MockWC):
        wc = MockWC.return_value
        wc.get_order.return_value = _walmart_order_payload(['Cancelled'])
        order = make_order(status=S.NEW)
        res = pipeline.sync_walmart_cancellations()
        order.refresh_from_db()
        self.assertEqual(res['cancelled'], 1)
        self.assertEqual(order.status, S.CANCELLED)

    @mock.patch.object(pipeline, 'WalmartClient')
    def test_sync_leaves_open_order_alone(self, MockWC):
        wc = MockWC.return_value
        wc.get_order.return_value = _walmart_order_payload(['Created'])
        order = make_order(status=S.NEW)
        pipeline.sync_walmart_cancellations()
        order.refresh_from_db()
        self.assertEqual(order.status, S.NEW)        # untouched

    def test_cancelled_order_is_archived(self):
        from apps.walmart_mcf.views import ARCHIVED_Q
        order = make_order(status=S.CANCELLED)
        self.assertTrue(
            WalmartOrder.objects.filter(ARCHIVED_Q, pk=order.pk).exists())

    def test_partial_tracking_uploaded_not_archived(self):
        """A TRACKING_UPLOADED order with no confirmed package is not archived
        (belt-and-braces: the pipeline only reaches this state when shipped)."""
        from apps.walmart_mcf.views import ARCHIVED_Q
        order = make_order(status=S.NEW)             # plain active order
        self.assertFalse(
            WalmartOrder.objects.filter(ARCHIVED_Q, pk=order.pk).exists())
