import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";

export const SUGGESTION_FIELDS = ["season", "starts_on", "ends_on", "market", "audience", "message", "category_url"];
const MANUAL_FIELDS = ["category_ids", "pinned_product_ids", "excluded_product_ids", "exclusions", "limits"];
const RESPONSE_KEYS = ["instruction", "fields", "evidence", "missing_fields", "manual_fields", "execution_authorized", "provider_writes", "persisted", "status"];
const LIMITS = { season: 160, starts_on: 10, ends_on: 10, market: 2, audience: 1000, message: 1000, category_url: 2000 };
const MARKETS = { SA: ["sa", "ksa", "saudi arabia", "السعودية", "السعوديه", "المملكة العربية السعودية"],
    QA: ["qa", "qatar", "قطر"], AE: ["ae", "uae", "united arab emirates", "الإمارات", "الامارات", "الإمارات العربية المتحدة"],
    KW: ["kw", "kuwait", "الكويت"], BH: ["bh", "bahrain", "البحرين"], OM: ["om", "oman", "عمان", "عُمان", "سلطنة عمان"], YE: ["ye", "yemen", "اليمن"] };
const invalid = () => { throw new Error("تعذر التحقق من اقتراح حقول الخطة. لم تتغير مدخلاتك."); };
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const sameKeys = (value, keys) => record(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
const sameList = (value, expected) => Array.isArray(value) && value.length === expected.length && new Set(value).size === value.length && value.every((key) => expected.includes(key));
const normalizeDigits = (value) => value.replace(/[٠-٩۰-۹]/g, (digit) => String(digit.charCodeAt(0) - (digit >= "۰" ? 0x06f0 : 0x0660)));

function dateValid(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
    const stamp = new Date(`${value}T00:00:00Z`);
    return !Number.isNaN(stamp.valueOf()) && stamp.toISOString().slice(0, 10) === value;
}

export function validateGoogleCommercialPlanSuggestion(data, instruction) {
    if (!sameKeys(data, RESPONSE_KEYS) || data.instruction !== instruction || data.status !== "unreviewed_suggestion"
        || data.execution_authorized !== false || data.provider_writes !== 0 || data.persisted !== false
        || !sameKeys(data.fields, SUGGESTION_FIELDS) || !sameList(data.manual_fields, MANUAL_FIELDS)) invalid();
    const present = SUGGESTION_FIELDS.filter((key) => data.fields[key] !== null);
    const missing = SUGGESTION_FIELDS.filter((key) => data.fields[key] === null);
    if (!sameKeys(data.evidence, present) || !sameList(data.missing_fields, missing)) invalid();
    for (const key of present) {
        const value = data.fields[key], quote = data.evidence[key];
        if (typeof value !== "string" || !value.trim() || value !== value.trim() || [...value].length > LIMITS[key]
            || typeof quote !== "string" || !quote.trim() || quote.length > 6000 || !instruction.includes(quote)) invalid();
        // Country names may be mapped to ISO codes. Other fields must retain
        // actual source text instead of accepting invented dates, links or copy.
        const source = key === "starts_on" || key === "ends_on" ? normalizeDigits(quote) : quote;
        if (key !== "market" && source !== value) invalid();
        if (key === "market" && !MARKETS[value]?.includes(quote.toLowerCase())) invalid();
        if ((key === "starts_on" || key === "ends_on") && !dateValid(value)) invalid();
        if (key === "category_url") {
            let url;
            try { url = new URL(value); } catch { invalid(); }
            if (url.protocol !== "https:" || !url.hostname || url.username || url.password || url.hash || /[\s\u0000-\u001f\u007f]/.test(value)) invalid();
        }
    }
    if (data.fields.starts_on && data.fields.ends_on && (data.fields.starts_on > data.fields.ends_on
        || (new Date(data.fields.ends_on) - new Date(data.fields.starts_on)) / 86400000 > 730)) invalid();
    return data;
}

export async function suggestGoogleCommercialPlan(instruction, { signal } = {}) {
    if (typeof instruction !== "string" || !instruction.trim() || [...instruction.trim()].length > 6000) {
        throw new Error("اكتب توجيه المالك أولًا، بحد أقصى 6000 حرف.");
    }
    const text = instruction.trim();
    const data = (await api.post(`${GOOGLE_ADS_ROOT}/commercial-plan-suggestions`, { instruction: text }, { signal })).data;
    return validateGoogleCommercialPlanSuggestion(data, text);
}

export function applyGoogleSuggestionToEmptyFields(form, suggestion) {
    // Revalidate at the application boundary and use an explicit allowlist.
    // No server response can add a budget, product selection or authorization.
    validateGoogleCommercialPlanSuggestion(suggestion, form.instruction.trim());
    const next = { ...form };
    for (const key of SUGGESTION_FIELDS) {
        if (!String(form[key] || "").trim() && suggestion.fields[key] !== null) next[key] = suggestion.fields[key];
    }
    return next;
}
