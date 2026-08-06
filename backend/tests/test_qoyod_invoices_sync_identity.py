from integrations.qoyod.qoyod_invoices_sync import _sync_salla_order_id


def test_synced_invoice_identity_is_stable_and_unique():
    assert _sync_salla_order_id("1110") == "qoyod-sync:1110"
    assert _sync_salla_order_id("1110") == _sync_salla_order_id("1110")
    assert _sync_salla_order_id("1110") != _sync_salla_order_id("1111")
