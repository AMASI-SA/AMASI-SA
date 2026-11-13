import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import CampaignHoverBadge from './CampaignHoverBadge';
import useCampaignHover from '../hooks/useCampaignHover';
import { campaignHoverKey, resolveOrderCampaign } from '../lib/orderCampaignAttribution';
import { getCampaignHoverBatch } from '../services/campaignHover';
jest.mock('../services/campaignHover', () => ({ getCampaignHoverBatch: jest.fn() }));

let host, root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers(); jest.setSystemTime(new Date('2026-11-13T12:30:00Z'));
    jest.clearAllMocks(); host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.useRealTimers(); });
const badge = { label: 'تيك توك', mark: 'T', className: '' };
function View({ orders }) {
    const reports = useCampaignHover(orders);
    return <CampaignHoverBadge badge={badge} campaign={resolveOrderCampaign(orders[0])} report={reports[campaignHoverKey(orders[0])]} />;
}
const show = async () => act(async () => host.querySelector('[role="button"]').click());
const settle = async () => act(async () => { jest.advanceTimersByTime(351); await Promise.resolve(); });

test('label-only card shows Salla totals without inventing account spend, and refreshes on a new same-label order', async () => {
    const first = { order_number: '30', utm_source: 'tiktok', utm_campaign: 'حملة الشتاء التجريبية' };
    const key = campaignHoverKey(first);
    const report = { campaign_id: null, commerce_scope: 'salla_utm_label', period_basis: 'salla_store_day',
        timezone: 'Asia/Riyadh', date: '2026-11-13', orders: 30, pieces: 40, sales_sar: 3000, spend_sar: null };
    getCampaignHoverBatch.mockResolvedValue({ [key]: report });
    await act(async () => root.render(<View orders={[first]} />));
    await settle(); await show();
    let tip = document.querySelector('[role="tooltip"]');
    expect(tip.textContent).toContain('30 طلب');
    expect(tip.textContent).toContain('40 قطعة');
    expect(tip.textContent).toContain('3,000.00 ر.س');
    expect(tip.textContent).toContain('بتوقيت المتجر');
    expect(tip.textContent).toContain('غير مربوط إعلانياً');
    expect(tip.textContent).toContain('غير مكتمل');
    getCampaignHoverBatch.mockResolvedValue({ [key]: { ...report, orders: 31, pieces: 41, sales_sar: 3100 } });
    await act(async () => root.render(<View orders={[{ ...first, order_number: '31' }, first]} />));
    await settle();
    tip = document.querySelector('[role="tooltip"]');
    expect(tip.textContent).toContain('31 طلب');
    expect(tip.textContent).toContain('3,100.00 ر.س');
    expect(getCampaignHoverBatch).toHaveBeenCalledTimes(2);
    await act(async () => { jest.advanceTimersByTime(600000); });
    expect(getCampaignHoverBatch).toHaveBeenCalledTimes(2);
});

test('native campaign name from server replaces unresolved ID title and observed Snap spend has a warning', async () => {
    await act(async () => root.render(<CampaignHoverBadge badge={badge}
        campaign={{ campaignId: '12345678901', campaignName: null, campaignDisplay: 'اسم الحملة غير متوفر' }}
        report={{ campaign_name: 'Verified native name', timezone: 'America/Los_Angeles', orders: 1,
            pieces: 4, sales_sar: 375, spend_sar: 75, cpa_sar: 75, roas: 5,
            spend_warning: 'snapchat_hour_coverage_unverified' }} />));
    await show(); const tip = document.querySelector('[role="tooltip"]');
    expect(tip.textContent).toContain('Verified native name');
    expect(tip.textContent).toContain('75.00 ر.س');
    expect(tip.textContent).toContain('اكتمال كل الساعات غير مثبت');
    expect(getCampaignHoverBatch).not.toHaveBeenCalled();
});
