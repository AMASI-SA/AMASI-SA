import api from "../lib/api";

export const MAX_SNAPCHAT_VIDEO_BYTES = 32_000_000;
const BASE = "/integrations-v2/snapchat_ads/management/media-uploads";
const TYPES = new Set(["video/mp4", "video/quicktime"]);
const STATUSES = new Set([
    "previewed", "receiving", "creating", "media_created", "awaiting_file",
    "uploading", "processing", "create_unknown", "upload_unknown", "ready",
]);
const FILE_STATES = new Set(["previewed", "awaiting_file"]);
const IDENTIFIER = /^[A-Za-z0-9_-]{1,160}$/;
const DIGEST = /^[0-9a-f]{64}$/;

function failure(code) {
    const error = new Error(code);
    error.code = code;
    return error;
}

function notAborted(signal) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
}

function contentType(file) {
    const supplied = String(file.type || "").toLowerCase();
    if (TYPES.has(supplied)) return supplied;
    if (!supplied && /\.mp4$/i.test(file.name)) return "video/mp4";
    if (!supplied && /\.mov$/i.test(file.name)) return "video/quicktime";
    throw failure("snapchat_media_content_type_mismatch");
}

export function validateSnapchatVideoFile(file) {
    if (typeof File === "undefined" || !(file instanceof File)) {
        throw failure("snapchat_media_file_required");
    }
    if (!Number.isSafeInteger(file.size) || file.size < 12 || file.size > MAX_SNAPCHAT_VIDEO_BYTES) {
        throw failure("snapchat_media_file_too_large");
    }
    if (!file.name || /[\\/\u0000-\u001f]/.test(file.name)) {
        throw failure("snapchat_media_file_invalid");
    }
    return contentType(file);
}

function readFile(file, signal) {
    if (typeof file.arrayBuffer === "function") return file.arrayBuffer();
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        const cleanup = () => signal?.removeEventListener("abort", abort);
        const abort = () => reader.abort();
        reader.onload = () => { cleanup(); resolve(reader.result); };
        reader.onerror = () => { cleanup(); reject(failure("snapchat_media_read_failed")); };
        reader.onabort = () => { cleanup(); reject(new DOMException("Aborted", "AbortError")); };
        signal?.addEventListener("abort", abort, { once: true });
        reader.readAsArrayBuffer(file);
    });
}

export async function prepareSnapchatVideoFile(file, { signal } = {}) {
    const type = validateSnapchatVideoFile(file);
    notAborted(signal);
    if (typeof globalThis.crypto?.subtle?.digest !== "function") {
        throw failure("snapchat_media_secure_hash_unavailable");
    }
    const bytes = await readFile(file, signal);
    notAborted(signal);
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    notAborted(signal);
    const sha256 = Array.from(new Uint8Array(digest))
        .map((value) => value.toString(16).padStart(2, "0")).join("");
    if (!DIGEST.test(sha256)) throw failure("snapchat_media_secure_hash_unavailable");
    return {
        name: Array.from(file.name).slice(0, 200).join(""),
        file_name: Array.from(file.name).slice(0, 180).join(""),
        size_bytes: file.size, content_type: type, sha256,
    };
}

export function normalizeSnapchatMediaUpload(payload, accountId) {
    const value = payload?.data || payload || {};
    const account = String(value.account_id || "");
    const uploadId = String(value.upload_id || "");
    const sha256 = String(value.sha256 || "");
    if (!IDENTIFIER.test(String(accountId || "")) || account !== accountId
        || value.provider !== "snapchat_ads" || !DIGEST.test(uploadId)
        || !DIGEST.test(sha256) || !Number.isSafeInteger(value.size_bytes)
        || value.size_bytes < 12 || value.size_bytes > MAX_SNAPCHAT_VIDEO_BYTES
        || !TYPES.has(value.content_type)) {
        throw failure("snapchat_media_response_scope_invalid");
    }
    const status = STATUSES.has(value.status) ? value.status : "unknown";
    const mediaId = IDENTIFIER.test(String(value.provider_media_id || ""))
        ? String(value.provider_media_id) : "";
    const attempted = value.provider_upload_attempted === true;
    return {
        provider: "snapchat_ads", account_id: account, upload_id: uploadId,
        name: String(value.name || ""), file_name: String(value.file_name || ""),
        size_bytes: value.size_bytes, sha256, content_type: value.content_type,
        ai_generated: value.ai_generated === true, status, provider_media_id: mediaId,
        provider_media_status: String(value.provider_media_status || ""),
        provider_upload_attempted: attempted,
        ready_for_creative: value.ready_for_creative === true && status === "ready"
            && value.provider_media_status === "READY" && attempted && Boolean(mediaId),
        can_upload: value.can_upload === true && FILE_STATES.has(status) && !attempted,
        confirm_token: typeof value.confirm_token === "string"
            && value.confirm_token.length <= 256 ? value.confirm_token : "",
        error_code: String(value.error_code || ""), recovery_action: String(value.recovery_action || ""),
    };
}

function sameSource(job, metadata) {
    if (!metadata || job.sha256 !== metadata.sha256 || job.size_bytes !== metadata.size_bytes
        || job.content_type !== metadata.content_type || job.file_name !== metadata.file_name) {
        throw failure("snapchat_media_file_digest_mismatch");
    }
}

function knownUploadId(uploadId) {
    if (!DIGEST.test(String(uploadId || ""))) throw failure("snapchat_media_upload_not_found");
    return uploadId;
}

export async function getSnapchatMediaProfiles({ accountId, signal }) {
    if (!IDENTIFIER.test(String(accountId || ""))) throw failure("snapchat_media_response_scope_invalid");
    const response = await api.get(BASE + "/profiles", {
        params: { account_id: accountId }, signal, timeout: 45_000,
    });
    const value = response?.data || response || {};
    if (value.provider !== "snapchat_ads" || value.account_id !== accountId
        || value.complete !== true || !Array.isArray(value.profiles) || value.profiles.length > 1000) {
        throw failure("snapchat_media_profile_catalog_incomplete");
    }
    const seen = new Set();
    return value.profiles.map((item) => {
        const id = String(item?.profile_id || "");
        if (!IDENTIFIER.test(id) || seen.has(id)) throw failure("snapchat_media_profile_catalog_incomplete");
        seen.add(id);
        return { profile_id: id, display_name: String(item.display_name || "ملف سناب").slice(0, 200) };
    });
}

export async function createSnapchatMediaUploadPreview({ accountId, metadata, aiGenerated = false, signal }) {
    if (!IDENTIFIER.test(String(accountId || ""))) throw failure("snapchat_media_response_scope_invalid");
    const response = await api.post(BASE + "/preview", {
        account_id: accountId, name: metadata.name, file_name: metadata.file_name,
        size_bytes: metadata.size_bytes, sha256: metadata.sha256,
        content_type: metadata.content_type, ai_generated: aiGenerated === true,
    }, { signal, timeout: 45_000, _mezanAuthRetried: true });
    const result = normalizeSnapchatMediaUpload(response, accountId);
    sameSource(result, metadata);
    if (result.ai_generated !== (aiGenerated === true)) throw failure("snapchat_media_response_scope_invalid");
    return result;
}

export async function uploadSnapchatVideoFile({ accountId, file, metadata, preview, signal, onProgress }) {
    const type = validateSnapchatVideoFile(file);
    const job = normalizeSnapchatMediaUpload(preview, accountId);
    sameSource(job, metadata);
    if (file.size !== metadata.size_bytes || type !== metadata.content_type) {
        throw failure("snapchat_media_file_digest_mismatch");
    }
    if (!job.can_upload || !job.confirm_token) throw failure("snapchat_media_reconciliation_required");
    // Disable the common Axios 401 POST replay. Unknown transport outcomes
    // are reconciled against this same job, never blindly uploaded again.
    const response = await api.post(BASE + "/" + knownUploadId(job.upload_id) + "/upload", file, {
        signal, timeout: 180_000, _mezanAuthRetried: true,
        transformRequest: [(value) => value],
        headers: { "Content-Type": type, "X-Mezan-Confirm-Token": job.confirm_token },
        onUploadProgress: (event) => {
            const loaded = Number(event.loaded);
            if (Number.isFinite(loaded) && loaded >= 0) {
                onProgress?.(Math.max(0, Math.min(100, Math.floor(loaded / file.size * 100))));
            }
        },
    });
    const result = normalizeSnapchatMediaUpload(response, accountId);
    sameSource(result, metadata);
    return result;
}

export async function reconcileSnapchatMediaUpload({ accountId, uploadId, signal }) {
    const response = await api.post(BASE + "/" + knownUploadId(uploadId) + "/reconcile", {},
        { signal, timeout: 45_000, _mezanAuthRetried: true });
    return normalizeSnapchatMediaUpload(response, accountId);
}

export async function getSnapchatMediaUpload({ accountId, uploadId, signal }) {
    const response = await api.get(BASE + "/" + knownUploadId(uploadId), { signal, timeout: 20_000 });
    return normalizeSnapchatMediaUpload(response, accountId);
}

export function snapchatMediaUploadError(error, fallback = "تعذّر إكمال رفع الفيديو.") {
    const code = error?.response?.data?.detail?.code || error?.code;
    return ({
        snapchat_media_file_required: "اختر ملف الفيديو من جهازك.",
        snapchat_media_file_too_large: "اختر فيديو صالحًا لا يتجاوز 32 ميجابايت.",
        snapchat_media_content_type_mismatch: "اختر فيديو بصيغة MP4 أو MOV.",
        snapchat_media_file_digest_mismatch: "الملف لا يطابق المعاينة؛ اختره من جديد.",
        snapchat_media_container_unsupported: "صيغة الفيديو غير مدعومة؛ استخدم MP4 حديثًا.",
        snapchat_media_secure_hash_unavailable: "تعذّر فحص الملف بأمان في هذا المتصفح.",
        snapchat_media_response_scope_invalid: "تعذّر إثبات ارتباط الفيديو بالحساب المحدد.",
        snapchat_media_creative_role_missing: "لا تتوفر صلاحية رفع الفيديو لهذا الحساب.",
        snapchat_media_upload_capacity_busy: "يوجد فيديو آخر قيد الرفع؛ حاول بعد اكتماله.",
        snapchat_campaign_mutations_disabled: "رفع الفيديو متوقف حاليًا لهذا الحساب.",
        snapchat_media_preview_expired: "انتهت المعاينة؛ أعد تجهيز الرفع.",
        snapchat_media_needs_reauth: "يحتاج اتصال سناب إلى تجديد.",
        snapchat_media_needs_reauth_or_role: "راجع اتصال سناب وصلاحية الحساب.",
        snapchat_media_create_unresolved: "لم يتأكد إنشاء الفيديو لدى سناب؛ الرفع متوقف لمنع التكرار.",
        snapchat_media_reconciliation_required: "افحص حالة الفيديو قبل أي محاولة أخرى.",
        snapchat_media_profile_catalog_incomplete: "تعذّر إكمال جلب ملفات سناب المتاحة لهذا الحساب.",
        snapchat_media_profile_catalog_unavailable: "تعذّر جلب ملفات سناب؛ راجع اتصال الحساب.",
    })[code] || fallback;
}
