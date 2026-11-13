"""Finalize the reviewed hover wiring after the pinned original patch."""
import ast
import hashlib
import subprocess
from pathlib import Path


def edit(path, old, new):
    target = Path(path)
    text = target.read_text()
    assert text.count(old) == 1, (path, old, text.count(old))
    target.write_text(text.replace(old, new, 1))


edit('frontend/src/pages/OrdersV2.jsx',
     'useCampaignHover(orders, { limit: 20, totals: orderTotals })',
     'useCampaignHover(orders, { limit: 20, totals: resultSummary })')

p = 'frontend/src/hooks/useCampaignHover.js'
edit(p, '            scalar(order?.amount_total ?? order?.total_amount_sar ?? order?.total_amount),', '''            scalar(order?.totals?.total_sar ?? order?.total_amount_sar),
            scalar(order?.totals?.total ?? order?.amount_total ?? order?.total_amount),
            scalar(order?.totals?.currency ?? order?.original_currency ?? order?.currency),
            scalar(order?.totals?.exchange_rate_to_sar ?? order?.exchange_rate_to_sar),''')
edit(p, 'items.map((item) => [scalar(item?.id ?? item?.product_id), scalar(item?.quantity)]),',
     'items.map((item) => [scalar(item?.order_item_id ?? item?.id ?? item?.product_id), scalar(item?.quantity), scalar(item?.type)]),')
edit(p, '["count", "orders_count", "total_orders", "total_count"]',
     '["count", "orders", "orders_count", "total_orders", "total_count", "paid_orders", "pending_payment_orders", "cancelled_orders", "refunded_orders", "units", "paid_sales"]')

edit('frontend/src/lib/orderCampaignAttribution.js',
     'order.utm?.source, source.platform, source.channel,',
     'order.utm?.source, source.utm_source, source.platform, source.channel,')

with Path('frontend/src/hooks/useCampaignHover.test.jsx').open('a') as out:
    out.write('''

test("canonical OrdersV2 money, item and result-summary changes invalidate the snapshot", () => {
    const dto = {
        order_number: "30", created_at: "2026-11-13T12:00:00Z", status: "processing",
        source: { campaign_id: CID, utm_source: "snapchat" },
        totals: { total: 100, total_sar: 100, currency: "SAR", exchange_rate_to_sar: "1" },
        items: [{ order_item_id: "line-1", quantity: 1, type: "product" }],
    };
    const base = campaignSnapshotRevision([dto], { orders: 30 }, NOW);
    for (const totals of [
        { ...dto.totals, total: 200 }, { ...dto.totals, total_sar: 200 },
        { ...dto.totals, currency: "QAR" }, { ...dto.totals, exchange_rate_to_sar: "1.03" },
    ]) expect(campaignSnapshotRevision([{ ...dto, totals }], { orders: 30 }, NOW)).not.toBe(base);
    expect(campaignSnapshotRevision([dto], { orders: 31 }, NOW)).not.toBe(base);
    expect(campaignSnapshotRevision([{ ...dto, items: [{ ...dto.items[0], type: "service" }] }],
        { orders: 30 }, NOW)).not.toBe(base);
    expect(campaignSnapshotRevision([{ ...dto, customer: { name: "private" },
        source: { ...dto.source, fetched_at: "2026-11-13T12:01:00Z" } }], { orders: 30 }, NOW)).toBe(base);
});
''')

with Path('frontend/src/lib/campaignHoverScopes.test.js').open('a') as out:
    out.write('''

test('canonical source.utm_source identifies the commerce-only label scope', () => {
    const label = 'حملة الشتاء التجريبية';
    expect(campaignHoverKey({ source: { utm_source: 'tiktok', utm_campaign: label } }))
        .toBe(campaignHoverKey({ utm_source: 'tiktok', utm_campaign: label }));
});
''')

p = '.github/workflows/campaign-hover-source-acceptance.yml'
edit(p, '      - backend/order_engine/campaign_hover.py', '''      - backend/order_engine/campaign_hover.py
      - backend/order_engine/campaign_hover_native_sources.py
      - backend/tests/test_campaign_hover_native_sources.py''')
edit(p, '      - frontend/src/components/CampaignHoverBadge.test.jsx', '''      - frontend/src/components/CampaignHoverBadge.test.jsx
      - frontend/src/components/CampaignHoverNativeSources.test.jsx
      - frontend/src/lib/orderCampaignAttribution.js
      - frontend/src/lib/orderCampaignAttribution.test.js
      - frontend/src/lib/campaignHoverScopes.test.js
      - frontend/src/pages/OrdersV2.navigation.test.jsx''')
edit(p, '          tests/test_campaign_hover_refresh.py', '''          tests/test_campaign_hover_refresh.py
          tests/test_campaign_hover_native_sources.py''')
edit(p, '          src/hooks/useCampaignHover.test.jsx', '''          src/hooks/useCampaignHover.test.jsx
          src/components/CampaignHoverNativeSources.test.jsx
          src/lib/orderCampaignAttribution.test.js
          src/lib/campaignHoverScopes.test.js
          src/pages/OrdersV2.navigation.test.jsx''')

expected = {
    'backend/order_engine/campaign_hover.py': 'd144f05257f461a1f4aa35e069102e96aa7f87c680ddf31a50d66e7e0b50c8f6',
    'frontend/src/pages/OrdersV2.jsx': '69d5ec3e0eed2a92c91925e95d01d9a9e12ab7c730f0c1d6fe13e29c2b1d39a0',
    'frontend/src/pages/AdvancedDashboard.jsx': '8d8b5aca7ad1e34f27e86a4cf899b7d8fa8440b4f7b6c1f8bc65f3f27007a4a1',
    'frontend/src/lib/orderCampaignAttribution.js': 'da04b3abe9bdbfa6d60efcb97da326111be977c962d6b59e83dd6a6a8b64d6da',
    'frontend/src/components/CampaignHoverBadge.jsx': '7207f6a142a59f53b82636abfa43d01f90f5252b4d3aafa3a47aa01a30f51d63',
    'frontend/src/hooks/useCampaignHover.js': 'fd2710a67740a2fec95ae2942a227bf162349dd38d3227d44d27b96d5cd9a91b',
    'frontend/src/hooks/useCampaignHover.test.jsx': 'c83136b1c648492998d1228aecc1ad6c9eb685adce4dada8d131cd42c2f29358',
    'frontend/src/lib/campaignHoverScopes.test.js': 'b436fa9ecffeea3cfdca3b5d039f6be3cad0ea6566cba440c6e1beb5ff2f4e48',
    '.github/workflows/campaign-hover-source-acceptance.yml': '489e6044e6919781d72dae148a0dd3877b0cdb1372c715e679c3197889a49574',
}
for path, digest in expected.items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, ('finalized_hash_mismatch', path)
changed = set(subprocess.check_output(['git', 'diff', '--name-only'], text=True).splitlines())
assert changed == set(expected), changed
ast.parse(Path('backend/order_engine/campaign_hover.py').read_text())
print('EXACT_FINALIZED_PATCH_PREPARED', sorted(changed))
