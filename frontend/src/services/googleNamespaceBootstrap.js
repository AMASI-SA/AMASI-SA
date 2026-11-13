import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";

export const BOOTSTRAP_LABELS = Object.freeze({ standing_policy: "إعدادات الإدارة", creation_policy: "إعدادات الإنشاء", account_guard: "حارس حدود الحساب", engine_probe: "فحص التخزين" });
const ROOT = `${GOOGLE_ADS_ROOT}/automation/namespace-bootstrap`;
const FALSE_FLAGS = ["execution_authorized", "funding_ready", "policy_configuration_verified", "legacy_bindings_verified", "future_permissions_guaranteed", "atomic_catalog_snapshot_verified", "new_uuid_lineage_verified"];
const COMMON = ["scope", "status", "namespace_contract_verified", "provider_writes", "business_document_writes", ...FALSE_FLAGS];
const RECEIPTS = ["no_preparation", "prepared", "reservation_owned_by_another_auth_context", "historical_completed_receipt", "reconciliation_required", "preparation_expired_retained"];
const FIELDS = ["request_nonce", "execution_token", "observation_digest", "manifest_digest"];
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const invalid = () => { throw new Error("google_namespace_bootstrap_response_unproved"); };
const hex = (value, count) => typeof value === "string" && new RegExp(`^[0-9a-f]{${count}}$`).test(value);
const labels = (value) => Array.isArray(value) && value.length >= 1 && value.length <= 4 && new Set(value).size === value.length && value.every((key) => Object.hasOwn(BOOTSTRAP_LABELS, key));
export function bootstrapExpiry(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value)) return NaN;
    const stamp = Date.parse(value);
    if (!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0, 19) !== value.slice(0, 19)) return NaN;
    return stamp;
}
export function validateBootstrapResult(data, operation = "receipt") {
    if (!object(data) || data.scope !== "fixed_namespace_bootstrap" || FALSE_FLAGS.some((key) => data[key] !== false)
        || data.provider_writes !== 0 || data.business_document_writes !== 0) invalid();
    const accepted = operation === "receipt" ? RECEIPTS : operation === "prepare" ? [...RECEIPTS.filter((s) => s !== "no_preparation"), "already_compatible_empty_noop"]
        : operation === "execute" ? ["verified_at_observation", "historical_completed_receipt", "reservation_owned_by_another_auth_context"] : [];
    if (!accepted.includes(data.status)) invalid();
    const fresh = ["verified_at_observation", "already_compatible_empty_noop"].includes(data.status);
    if (data.namespace_contract_verified !== fresh) invalid();
    const allowed = [...COMMON];
    if (RECEIPTS.includes(data.status)) { allowed.push("execution_permitted"); if (data.execution_permitted !== false) invalid(); }
    if (data.status === "prepared") {
        allowed.push(...FIELDS, "preparation_token_available", "expires_at", "would_create");
        if (data.preparation_token_available !== true || !hex(data.request_nonce, 32) || FIELDS.slice(1).some((key) => !hex(data[key], 64))
            || !Number.isFinite(bootstrapExpiry(data.expires_at)) || !labels(data.would_create)) invalid();
    }
    if (data.status === "verified_at_observation") {
        allowed.push("receipt_retained", "execution_token_consumed", "created_labels");
        if (data.receipt_retained !== true || data.execution_token_consumed !== true || !labels(data.created_labels)) invalid();
    }
    if (Object.keys(data).length !== allowed.length || Object.keys(data).some((key) => !allowed.includes(key))) invalid();
    return { ...data, ...(data.would_create ? { would_create: [...data.would_create] } : {}), ...(data.created_labels ? { created_labels: [...data.created_labels] } : {}) };
}
const options = (signal) => ({ signal, timeout: 30000, _mezanAuthRetried: true });
export function newBootstrapNonce() {
    if (typeof globalThis.crypto?.getRandomValues !== "function") throw new Error("google_namespace_bootstrap_randomness_unavailable");
    const bytes = new Uint8Array(16); globalThis.crypto.getRandomValues(bytes);
    return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
}
export async function readGoogleBootstrapReceipt({ signal } = {}) {
    return validateBootstrapResult((await api.get(`${ROOT}/receipt`, options(signal))).data, "receipt");
}
export async function prepareGoogleNamespaceBootstrap(nonce, { signal } = {}) {
    if (!hex(nonce, 32)) invalid();
    return validateBootstrapResult((await api.post(`${ROOT}/prepare`, { request_nonce: nonce, acknowledge_preparation_only: true }, options(signal))).data, "prepare");
}
export async function executeGoogleNamespaceBootstrap(candidate, { signal } = {}) {
    const prepared = validateBootstrapResult(candidate, "receipt");
    if (prepared.status !== "prepared" || bootstrapExpiry(prepared.expires_at) <= Date.now()) invalid();
    const body = Object.fromEntries(FIELDS.map((key) => [key, prepared[key]]));
    return validateBootstrapResult((await api.post(`${ROOT}/execute`, { ...body, acknowledge_storage_only: true }, options(signal))).data, "execute");
}
