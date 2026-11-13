function isPresent(value) {
    return value !== null && value !== undefined && value !== "";
}

function firstPresent(...values) {
    return values.find(isPresent);
}

function asText(value) {
    if (!isPresent(value)) return null;
    const text = String(value).trim();
    return text || null;
}

export function looksLikeCampaignId(value) {
    const text = asText(value);
    if (!text) return false;
    return /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(text)
        || /^\d{6,}$/.test(text);
}

export function resolveOrderCampaign(order = {}) {
    const sourceObject = typeof order?.source === "object" && order.source
        ? order.source
        : {};
    const attribution = firstPresent(
        order?.utm,
        order?.marketing,
        order?.attribution,
        sourceObject.attribution,
        sourceObject.utm,
    ) || {};
    const rawCampaign = firstPresent(
        order?.utm_campaign,
        sourceObject.utm_campaign,
        attribution.campaign,
        attribution.utm_campaign,
    );
    const explicitCampaignId = asText(firstPresent(
        order?.campaign_id,
        sourceObject.campaign_id,
        looksLikeCampaignId(rawCampaign) ? rawCampaign : null,
    ));
    const campaignId = isClickOriginPlaceholder(explicitCampaignId) ? null : explicitCampaignId;
    const campaignNameCandidate = asText(firstPresent(
        order?.campaign_name,
        sourceObject.campaign_name,
        attribution.campaign_name,
    ));
    const campaignName = campaignNameCandidate
        && !isClickOriginPlaceholder(campaignNameCandidate)
        && campaignNameCandidate !== campaignId
        ? campaignNameCandidate
        : null;
    const rawCampaignText = asText(rawCampaign);

    return {
        campaignId,
        campaignName,
        rawCampaign: rawCampaignText,
        campaignDisplay: campaignName
            || (campaignId || isClickOriginPlaceholder(rawCampaignText) ? "اسم الحملة غير متوفر" : rawCampaignText),
    };
}

export function isClickOriginPlaceholder(value) {
    return /^using\s+the\s+clickid\b/i.test(String(value || "").trim());
}

// A separate commerce-only scope, never an ad-platform identity or spend join.
// Server validates source + exact UTM label against tenant-owned Salla rows.
export function campaignHoverKey(order = {}) {
    const campaign = resolveOrderCampaign(order);
    if (campaign.campaignId) return campaign.campaignId;
    const source = typeof order.source === "object" && order.source ? order.source : {};
    const values = [order.utm_source, order.utm?.source, source.utm_source, source.platform, source.channel,
        source.source, typeof order.source === "string" ? order.source : "",
        order.marketing?.source, order.attribution?.source, order.source_channel];
    const aliases = { tiktok: "tiktok", "tik tok": "tiktok", google: "google", google_ads: "google",
        adwords: "google", gads: "google", snapchat: "snapchat", snap: "snapchat",
        facebook: "meta", instagram: "meta", meta: "meta", fb: "meta", ig: "meta" };
    const providers = [...new Set(values.map(value => aliases[String(value || "").trim().toLowerCase()]).filter(Boolean))];
    const provider = providers.length === 1 ? providers[0] : null;
    // Resolved/display names alone are not evidence of a UTM label.
    const label = campaign.rawCampaign;
    if (!provider || !label || label.length > 180 || /[\u0000-\u001f]/.test(label) || isClickOriginPlaceholder(label)) return "";
    try {
        const bytes = encodeURIComponent(label).replace(/%([0-9A-F]{2})/g,
            (_, hex) => String.fromCharCode(parseInt(hex, 16)));
        const token = btoa(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
        return `salla:${provider}:${token}`;
    } catch { return ""; }
}
