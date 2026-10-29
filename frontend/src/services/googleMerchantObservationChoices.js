import api from "../lib/api";

const ENDPOINT = "/integrations-v2/google_ads/merchant-products/choices";
const FALSE_FLAGS = ["remote_account_set_complete", "native_link_verified", "merchant_access_verified",
    "continuous_authorization_proved", "store_product_mapping_verified", "ads_eligibility_verified", "execution_authorized"];
function record(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function id(value) {
    return typeof value === "string" && /^[1-9][0-9]{0,18}$/.test(value)
        && (value.length < 19 || value <= "9223372036854775807");
}
function unproved() { throw new Error("GOOGLE_MERCHANT_CHOICES_UNPROVED"); }

export function validateGoogleMerchantChoices(value, ownerId) {
    if (!record(value) || typeof ownerId !== "string" || !ownerId || value.owner_id !== ownerId
        || value.source !== "mezan_google_local_membership" || value.local_set_complete !== true
        || value.local_membership !== "matching_complete_bracketing_snapshots"
        || FALSE_FLAGS.some((field) => value[field] !== false) || value.provider_calls !== 0 || value.provider_writes !== 0
        || typeof value.observation_id !== "string" || !/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value.observation_id)
        || typeof value.observed_at !== "string" || !/^\d{4}-\d{2}-\d{2}T/.test(value.observed_at) || !Number.isFinite(Date.parse(value.observed_at))
        || !Array.isArray(value.ads_accounts) || !Array.isArray(value.merchant_accounts)
        || !Number.isInteger(value.local_account_count) || value.local_account_count < 0 || value.local_account_count > 400
        || value.ads_accounts.length + value.merchant_accounts.length !== value.local_account_count) unproved();
    function rows(source, key) {
        const seen = new Set();
        return source.map((row) => {
            if (!record(row) || Object.keys(row).length !== 2 || !id(row[key]) || seen.has(row[key])
                || typeof row.display_name !== "string" || !row.display_name.length || row.display_name.length > 256
                || /[\u0000-\u001f\u007f]/u.test(row.display_name)) unproved();
            seen.add(row[key]);
            return Object.freeze({ [key]: row[key], display_name: row.display_name });
        });
    }
    return Object.freeze({ ownerId, observationId: value.observation_id, observedAt: value.observed_at,
        adsAccounts: Object.freeze(rows(value.ads_accounts, "account_id")),
        merchantAccounts: Object.freeze(rows(value.merchant_accounts, "merchant_id")) });
}

export async function getGoogleMerchantChoices({ ownerId, signal }) {
    const response = await api.get(ENDPOINT, { signal, timeout: 30000, _mezanAuthRetried: true });
    return validateGoogleMerchantChoices(response.data, ownerId);
}
