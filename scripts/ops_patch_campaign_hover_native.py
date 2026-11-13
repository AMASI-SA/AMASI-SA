"""Apply the locally reviewed reader patch to the pinned task checkout only."""
import ast
import hashlib
import subprocess
from pathlib import Path


def edit(path, old, new):
    target = Path(path)
    text = target.read_text()
    assert text.count(old) == 1, (path, old[:100], text.count(old))
    target.write_text(text.replace(old, new, 1))


p = 'frontend/src/lib/orderCampaignAttribution.js'
edit(p, '        || /^\\d{12,}$/.test(text)\n        || text.length >= 24;', '        || /^\\d{6,}$/.test(text);')
edit(p, '    const campaignId = asText(firstPresent(', '    const explicitCampaignId = asText(firstPresent(')
edit(p, '    const campaignNameCandidate = asText(firstPresent(', '    const campaignId = isClickOriginPlaceholder(explicitCampaignId) ? null : explicitCampaignId;\n    const campaignNameCandidate = asText(firstPresent(')
edit(p, '        && campaignNameCandidate !== campaignId', '        && !isClickOriginPlaceholder(campaignNameCandidate)\n        && campaignNameCandidate !== campaignId')
edit(p, '            || (campaignId ? "اسم الحملة غير متوفر" : rawCampaignText),', '            || (campaignId || isClickOriginPlaceholder(rawCampaignText) ? "اسم الحملة غير متوفر" : rawCampaignText),')
with Path(p).open('a') as out:
    out.write(r'''
export function isClickOriginPlaceholder(value) {
    return /^using\s+the\s+clickid\b/i.test(String(value || "").trim());
}

// A separate commerce-only scope, never an ad-platform identity or spend join.
// Server validates source + exact UTM label against tenant-owned Salla rows.
export function campaignHoverKey(order = {}) {
    const campaign = resolveOrderCampaign(order);
    if (campaign.campaignId) return campaign.campaignId;
    const source = typeof order.source === "object" && order.source ? order.source : {};
    const values = [order.utm_source, order.utm?.source, source.platform, source.channel,
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
''')
p = 'frontend/src/hooks/useCampaignHover.js'
edit(p, 'import { resolveOrderCampaign }', 'import { campaignHoverKey }')
edit(p, 'resolveOrderCampaign(order || {}).campaignId || ""', 'campaignHoverKey(order || {})')
edit(p, 'resolveOrderCampaign(order).campaignId', 'campaignHoverKey(order)')
edit(p, 'const COALESCE_MS = 350;', 'const COALESCE_MS = 350;\nconst EMPTY_TOTALS = Object.freeze({});')
edit(p, 'visibleCount = Infinity, totals = {}', 'visibleCount = Infinity, totals = EMPTY_TOTALS')
for p in ('frontend/src/pages/OrdersV2.jsx', 'frontend/src/pages/AdvancedDashboard.jsx'):
    edit(p, 'import { resolveOrderCampaign }', 'import { resolveOrderCampaign, campaignHoverKey }')
    edit(p, 'campaignHover[resolveOrderCampaign(order).campaignId]', 'campaignHover[campaignHoverKey(order)]')
p = 'frontend/src/components/CampaignHoverBadge.jsx'
edit(p, '    if (!campaign?.campaignId) return "لا يوجد معرّف حملة موثوق في بيانات هذا الطلب من سلة.";', '''    if (report?.commerce_scope === "salla_utm_label") {
        return "طلبات ومبيعات سلة مجمّعة بحسب اسم التتبع المسجل، وليست مطابقة مؤكدة لحملة إعلانية؛ الصرف وتكلفة الطلب والعائد غير متاحة لهذا التجميع.";
    }
    if (report?.spend_warning === "snapchat_hour_coverage_unverified" && known(report?.spend_sar)) {
        return "الصرف المتوفر من تقرير سناب بيوم الحساب؛ اكتمال كل الساعات غير مثبت، وتكلفة الطلب والعائد أوليان وقابلان للتحديث.";
    }
    if (!campaign?.campaignId && report === undefined) return "لا يوجد معرّف حملة موثوق؛ بيانات مصدر النقر العامة لا تحدد حملة بعينها.";''')
edit(p, '    if (reason === "campaign_account_unavailable")', '    if (reason === "campaign_identity_missing") return "بيانات مصدر النقر العامة لا تتضمن معرّف الحملة؛ لا يمكن نسبتها إلى حملة محددة.";\n    if (reason === "campaign_account_unavailable")')
edit(p, '    const displayName = campaign?.campaignDisplay || report?.campaign_name || "الحملة غير محددة";', '    const displayName = campaign?.campaignName || report?.campaign_name || campaign?.campaignDisplay || "الحملة غير محددة";')
edit(p, '                    اليوم من 12:00 ص بتوقيت الحساب: <bdi', '                    {report?.period_basis === "salla_store_day" ? "اليوم من 12:00 ص بتوقيت المتجر: " : "اليوم من 12:00 ص بتوقيت الحساب: "}<bdi')
edit(p, '                <table className="w-full border-collapse">', '''                {report?.commerce_scope === "salla_utm_label" && <div className="mb-2 text-[10px] font-bold text-amber-700">تجميع اسم التتبع في سلة · غير مربوط إعلانياً</div>}
                <table className="w-full border-collapse">''')
p = 'backend/order_engine/campaign_hover.py'
edit(p, 'import asyncio\n', 'import asyncio\nimport base64\nimport binascii\nimport re\n')
edit(p, '    SALLA_RAW_ATTRIBUTION_PROJECTION, canonical_ad_platform, campaign_id_candidates,', '    SALLA_RAW_ATTRIBUTION_PROJECTION, canonical_ad_platform, campaign_id_candidates, field_values,')
edit(p, 'def _salla_query(user_id, campaign_ids, start, now):', r'''def _label_scope(key):
    if not key.startswith("salla:"):
        return None
    parts = key.split(":", 2)
    if len(parts) != 3 or parts[1] not in PROVIDERS or not re.fullmatch(r"[A-Za-z0-9_-]{1,960}", parts[2]):
        raise HTTPException(status_code=400, detail="invalid_campaign_scope")
    try:
        label = base64.b64decode(parts[2] + "=" * (-len(parts[2]) % 4), altchars=b"-_", validate=True).decode("utf-8")
    except (ValueError, UnicodeError, binascii.Error):
        raise HTTPException(status_code=400, detail="invalid_campaign_scope") from None
    if not label or label != label.strip() or len(label) > 180 or any(ord(c) < 32 for c in label) or re.match(r"using\s+the\s+clickid\b", label, re.I):
        raise HTTPException(status_code=400, detail="invalid_campaign_scope")
    return parts[1], label


def _salla_query(user_id, campaign_ids, start, now):''')
edit(p, '    values = list(campaign_ids)', '    values = [scope[1] if (scope := _label_scope(cid)) else cid for cid in campaign_ids]')
edit(p, '    result = {cid: _blank(cid) for cid in campaign_ids}', r'''    labels = {cid: scope for cid in campaign_ids if (scope := _label_scope(cid))}
    blocked = {cid for cid in campaign_ids if re.match(r"using\s+the\s+clickid\b", cid, re.I)}
    platform_ids = [cid for cid in campaign_ids if cid not in labels and cid not in blocked]
    # Label-only commerce has no verified ad account. Its explicitly labelled
    # store day must not masquerade as an inferred advertising-account day.
    label_window = _window(now, "Asia/Riyadh") if labels else None
    result = {cid: _blank(cid) for cid in campaign_ids}''')
edit(p, '    if not old_ok or not native_ok:\n        return {"as_of": now.isoformat(), "campaigns": {cid: _blank(cid, "account_read_incomplete") for cid in campaign_ids}}', '''    if not old_ok or not native_ok:
        if not labels:
            return {"as_of": now.isoformat(), "campaigns": {cid: _blank(cid, "account_read_incomplete") for cid in campaign_ids}}
        legacy, native = [], []''')
edit(p, '    if not dates:\n', '    if not dates and not labels:\n')
edit(p, '        if cid in identities and key in accounts:', '        if cid in identities and cid not in labels and cid not in blocked and key in accounts:')
edit(p, '    usable = {cid: next(iter(keys)) for cid, keys in identities.items() if len(keys) == 1}', '''    from .campaign_hover_native_sources import read_native_campaign_sources
    native_identities, native_spends, extra_health = await read_native_campaign_sources(
        db, user_id, platform_ids, accounts, windows, now)
    catalog_ok = catalog_ok and extra_health
    for cid, key, name in native_identities:
        identify(cid, key, name)
    for cid, key, spend in native_spends:
        if identify(cid, key):
            offer(cid, key, spend, 3)

    usable = {cid: next(iter(keys)) for cid, keys in identities.items() if len(keys) == 1}''')
edit(p, '    starts = [window[0] for window in windows.values() if window]', '    starts = [window[0] for window in windows.values() if window]\n    if label_window:\n        starts.append(label_window[0])')
edit(p, '        cids = set(campaign_id_candidates(order)).intersection(campaign_ids)', '''        cids = set(campaign_id_candidates(order)).intersection(platform_ids)
        raw_provider = canonical_ad_platform(raw_order)
        raw_labels = set(field_values(raw_order, "utm_campaign", "campaign"))
        # No fuzzy/normalized name join and no reliance on product/display names.
        cids.update(cid for cid, (label_provider, label) in labels.items()
                    if raw_provider == label_provider and raw_labels == {label})''')
edit(p, '        for cid in cids:\n            if len(identities[cid]) > 1:', '''        for cid in cids:
            if cid in labels:
                if at < label_window[0]:
                    continue
                metric = totals[cid]
                metric["orders"] += 1
                pieces = _quantity(raw_order)
                sales = _amount(salla_order_currency_fields(raw_order).get("total_amount_sar"))
                if pieces is None:
                    metric["pieces_ok"] = False
                else:
                    metric["pieces"] += pieces
                if sales is None:
                    metric["sales_ok"] = False
                else:
                    metric["sales"] += sales
                continue
            if len(identities[cid]) > 1:''')
edit(p, '        record = result[cid]\n        if len(identities[cid]) > 1:', '''        record = result[cid]
        if cid in blocked:
            record["status"] = "campaign_identity_missing"
            continue
        if cid in labels:
            metric = totals[cid]
            okay = commerce_ok and metric["time_ok"]
            record.update(campaign_id=None, scope_key=cid, commerce_scope="salla_utm_label",
                period_basis="salla_store_day", campaign_name=labels[cid][1],
                provider=labels[cid][0], account_id=None, timezone="Asia/Riyadh",
                date=label_window[1], period_start=label_window[0].isoformat(),
                orders_as_of=now.isoformat(), orders=metric["orders"] if okay else None,
                pieces=metric["pieces"] if okay and metric["pieces_ok"] else None,
                sales_sar=round(metric["sales"], 2) if okay and metric["sales_ok"] else None,
                spend_reason="campaign_label_not_linked", status="partial")
            continue
        if len(identities[cid]) > 1:''')
edit(p, 'max_length=1500)', 'max_length=22000)')
edit(p, 'any(len(x) > 160 for x in ids)', 'any(len(x) > (1000 if x.startswith("salla:") else 160) for x in ids)')

expected = {
    'backend/order_engine/campaign_hover.py': 'd144f05257f461a1f4aa35e069102e96aa7f87c680ddf31a50d66e7e0b50c8f6',
    'frontend/src/pages/OrdersV2.jsx': '468ba53421ee56c21ae8c33dcb27fc97cfddfc97d5a6fb0c44341512740fc4dd',
    'frontend/src/pages/AdvancedDashboard.jsx': '8d8b5aca7ad1e34f27e86a4cf899b7d8fa8440b4f7b6c1f8bc65f3f27007a4a1',
    'frontend/src/lib/orderCampaignAttribution.js': 'a81794b315b952df2ffa9727e3851be5448404d20269a6fcf0748d3950acf046',
    'frontend/src/components/CampaignHoverBadge.jsx': '7207f6a142a59f53b82636abfa43d01f90f5252b4d3aafa3a47aa01a30f51d63',
    'frontend/src/hooks/useCampaignHover.js': '364bc49055414af677777baf770f65bd6ea43edfde7499cdb949ecd49919860f',
}
for path, digest in expected.items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, ('reviewed_hash_mismatch', path)
for path in ('backend/order_engine/campaign_hover.py', 'backend/order_engine/campaign_hover_native_sources.py', 'backend/tests/test_campaign_hover_native_sources.py'):
    ast.parse(Path(path).read_text())
changed = set(subprocess.check_output(['git', 'diff', '--name-only'], text=True).splitlines())
assert changed == set(expected), changed
print('EXACT_REVIEWED_PATCH_PREPARED', sorted(changed))
