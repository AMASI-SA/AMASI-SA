import api from "../lib/api";

export const AD_COST_PROVIDER_ORDER = Object.freeze([
    "snapchat_ads",
    "tiktok_ads",
    "meta_ads",
    "google_ads",
]);

export const AD_COST_PROVIDER_LABELS = Object.freeze({
    snapchat_ads: "Snapchat",
    tiktok_ads: "TikTok",
    meta_ads: "Meta",
    google_ads: "Google Ads",
});

const SAFE_PROVIDERS = new Set(AD_COST_PROVIDER_ORDER);
const SAFE_CURRENCIES = new Set(["SAR", "USD"]);

function text(value, fallback = "") {
    return typeof value === "string" ? value : fallback;
}

function nullableText(value) {
    const normalized = text(value).trim();
    return normalized || null;
}

function number(value, fallback = null, { min = null, max = null } = {}) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    if (min !== null && parsed < min) return fallback;
    if (max !== null && parsed > max) return fallback;
    return parsed;
}

export function normalizeAdAccountCostSettingsV2(payload = {}) {
    const source = payload?.data && typeof payload.data === "object"
        ? payload.data
        : payload;
    const rawItems = Array.isArray(source?.items) ? source.items : [];
    const items = rawItems
        .filter((row) => row && typeof row === "object")
        .map((row) => {
            const accountId = nullableText(row.mezan_integration_account_id);
            const provider = SAFE_PROVIDERS.has(row.provider) ? row.provider : null;
            const externalId = nullableText(row.external_account_id);
            if (!accountId || !provider || !externalId) return null;
            const nativeCurrency = SAFE_CURRENCIES.has(row.native_currency)
                ? row.native_currency
                : "SAR";
            return {
                mezan_integration_account_id: accountId,
                provider,
                provider_label: text(
                    row.provider_label,
                    AD_COST_PROVIDER_LABELS[provider],
                ),
                external_account_id: externalId,
                display_name: text(row.display_name, externalId),
                provider_currency: SAFE_CURRENCIES.has(row.provider_currency)
                    ? row.provider_currency
                    : null,
                timezone: nullableText(row.timezone),
                connection_status: text(row.connection_status, "unknown"),
                selected: row.selected === true,
                last_sync_at: nullableText(row.last_sync_at),
                native_currency: nativeCurrency,
                exchange_rate_to_sar: nativeCurrency === "SAR"
                    ? 1
                    : number(row.exchange_rate_to_sar, 3.7544, { min: 0.0001, max: 20 }),
                bank_commission_pct: number(
                    row.bank_commission_pct,
                    provider === "snapchat_ads" ? 2.3 : 0,
                    { min: 0, max: 20 },
                ),
                apply_bank_commission: row.apply_bank_commission === true,
                configured: row.configured === true,
                updated_at: nullableText(row.updated_at),
            };
        })
        .filter(Boolean)
        .sort((left, right) => {
            const providerDelta = AD_COST_PROVIDER_ORDER.indexOf(left.provider)
                - AD_COST_PROVIDER_ORDER.indexOf(right.provider);
            if (providerDelta !== 0) return providerDelta;
            return left.display_name.localeCompare(right.display_name, "ar");
        });
    const summary = source?.summary && typeof source.summary === "object"
        ? source.summary
        : {};
    return {
        generated_at: nullableText(source?.generated_at),
        items,
        summary: {
            accounts_total: number(summary.accounts_total, items.length, { min: 0 }) || 0,
            configured: number(
                summary.configured,
                items.filter((item) => item.configured).length,
                { min: 0 },
            ) || 0,
            fee_enabled: number(
                summary.fee_enabled,
                items.filter((item) => item.apply_bank_commission).length,
                { min: 0 },
            ) || 0,
            usd_accounts: number(
                summary.usd_accounts,
                items.filter((item) => item.native_currency === "USD").length,
                { min: 0 },
            ) || 0,
            sar_accounts: number(
                summary.sar_accounts,
                items.filter((item) => item.native_currency === "SAR").length,
                { min: 0 },
            ) || 0,
        },
        policy: {
            legacy_counterparties_read: source?.policy?.legacy_counterparties_read === true,
            legacy_ads_currency_settings_read: source?.policy?.legacy_ads_currency_settings_read === true,
            accounting_write_reached: source?.policy?.accounting_write_reached === true,
            qoyod_write_reached: source?.policy?.qoyod_write_reached === true,
        },
    };
}

export async function getAdAccountCostSettingsV2() {
    const response = await api.get("/ads-manager/account-cost-settings");
    return normalizeAdAccountCostSettingsV2(response.data);
}

export async function saveAdAccountCostSettingsV2(accountId, values) {
    const nativeCurrency = SAFE_CURRENCIES.has(values?.native_currency)
        ? values.native_currency
        : "SAR";
    const payload = {
        native_currency: nativeCurrency,
        exchange_rate_to_sar: nativeCurrency === "SAR"
            ? 1
            : number(values?.exchange_rate_to_sar, 3.7544, { min: 0.0001, max: 20 }),
        bank_commission_pct: number(
            values?.bank_commission_pct,
            0,
            { min: 0, max: 20 },
        ),
        apply_bank_commission: values?.apply_bank_commission === true,
    };
    const response = await api.put(
        `/ads-manager/account-cost-settings/${encodeURIComponent(accountId)}`,
        payload,
    );
    return normalizeAdAccountCostSettingsV2({ items: [response.data] }).items[0];
}
