import { campaignHoverKey, resolveOrderCampaign, looksLikeCampaignId } from './orderCampaignAttribution';

test('long readable Arabic labels remain commerce-only keys, not campaign IDs', () => {
    const label = 'حملة الشتاء التجريبية للفروات والهدايا';
    const order = { source: { channel: 'tiktok', utm_campaign: label } };
    expect(looksLikeCampaignId(label)).toBe(false);
    expect(resolveOrderCampaign(order).campaignId).toBeNull();
    expect(campaignHoverKey(order)).toMatch(/^salla:tiktok:[A-Za-z0-9_-]+$/);
    expect(campaignHoverKey({ ...order })).toBe(campaignHoverKey(order));
});

test('Google click-source placeholders never become a campaign ID or a commerce-label group', () => {
    const label = 'Using the CLICKID gad_source provided by google';
    const order = { utm_source: 'google', utm_campaign: label, campaign_id: label, campaign_name: label };
    expect(resolveOrderCampaign(order).campaignId).toBeNull();
    expect(resolveOrderCampaign(order).campaignName).toBeNull();
    expect(resolveOrderCampaign(order).campaignDisplay).toBe('اسم الحملة غير متوفر');
    expect(campaignHoverKey(order)).toBe('');
});

test('real numeric Google IDs and explicit provider IDs remain financial identity keys', () => {
    expect(campaignHoverKey({ utm_source: 'google', utm_campaign: '12345678901' })).toBe('12345678901');
    expect(campaignHoverKey({ campaign_id: 'test-explicit-id', campaign_name: 'Winter' })).toBe('test-explicit-id');
});

test('display name alone, missing provider, conflicting provider, and oversized labels are not guessed', () => {
    expect(campaignHoverKey({ campaign_name: 'Winter', utm_source: 'tiktok' })).toBe('');
    expect(campaignHoverKey({ utm_campaign: 'Winter' })).toBe('');
    expect(campaignHoverKey({ utm_source: 'google', source: { channel: 'tiktok' }, utm_campaign: 'Winter' })).toBe('');
    expect(campaignHoverKey({ utm_source: 'tiktok', utm_campaign: 'x'.repeat(181) })).toBe('');
    expect(campaignHoverKey({ utm_source: 'tiktok', utm_campaign: 'line\nbreak' })).toBe('');
});


test('canonical source.utm_source identifies the commerce-only label scope', () => {
    const label = 'حملة الشتاء التجريبية';
    expect(campaignHoverKey({ source: { utm_source: 'tiktok', utm_campaign: label } }))
        .toBe(campaignHoverKey({ utm_source: 'tiktok', utm_campaign: label }));
});
