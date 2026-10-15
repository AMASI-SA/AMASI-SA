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
        || /^\d{12,}$/.test(text)
        || text.length >= 24;
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
    const campaignId = asText(firstPresent(
        order?.campaign_id,
        sourceObject.campaign_id,
        looksLikeCampaignId(rawCampaign) ? rawCampaign : null,
    ));
    const campaignNameCandidate = asText(firstPresent(
        order?.campaign_name,
        sourceObject.campaign_name,
        attribution.campaign_name,
    ));
    const campaignName = campaignNameCandidate
        && campaignNameCandidate !== campaignId
        ? campaignNameCandidate
        : null;
    const rawCampaignText = asText(rawCampaign);

    return {
        campaignId,
        campaignName,
        rawCampaign: rawCampaignText,
        campaignDisplay: campaignName
            || (campaignId ? "اسم الحملة غير متوفر" : rawCampaignText),
    };
}
