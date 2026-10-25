// File bytes stay in this browser. Never pass them to Mezan's API client.
export const MAX_FILE_BYTES = 1024 * 1024 * 1024;
export const CHUNK_BYTES = 10 * 1024 * 1024;
export const FINGERPRINT_BLOCK = 4 * 1024 * 1024;
const HOSTS = new Set(["open-upload.tiktokapis.com", "upload.us.tiktokapis.com", "upload.eu.tiktokapis.com"]);
const MIME = { mp4: "video/mp4", mov: "video/quicktime", webm: "video/webm" };
const abortCheck = (signal) => { if (signal?.aborted) throw new DOMException("أُوقف نقل الفيديو؛ راجع حالة العملية.", "AbortError"); };

export function fileType(file) {
    const mime = file?.type || MIME[file?.name?.split(".").pop()?.toLowerCase()];
    if (!Object.values(MIME).includes(mime) || !Number.isSafeInteger(file?.size) || file.size < 1 || file.size > MAX_FILE_BYTES) {
        throw new Error("اختر فيديو MP4 أو MOV أو WebM بحجم لا يتجاوز 1GB.");
    }
    return mime;
}

export function chunkPlan(size) {
    if (!Number.isSafeInteger(size) || size < 1 || size > MAX_FILE_BYTES) throw new Error("حجم الفيديو غير صالح.");
    const chunk = Math.min(CHUNK_BYTES, size);
    return { source: "FILE_UPLOAD", video_size: size, chunk_size: chunk, total_chunk_count: Math.max(1, Math.floor(size / chunk)) };
}

export async function fingerprintFile(file, { signal, onProgress = () => {} } = {}) {
    fileType(file); abortCheck(signal);
    const header = new TextEncoder().encode(`tiktok-file-manifest-v1\0${file.size}\0${FINGERPRINT_BLOCK}\0`);
    const hashes = [];
    for (let start = 0; start < file.size; start += FINGERPRINT_BLOCK) {
        abortCheck(signal);
        const bytes = await file.slice(start, Math.min(start + FINGERPRINT_BLOCK, file.size)).arrayBuffer();
        hashes.push(new Uint8Array(await window.crypto.subtle.digest("SHA-256", bytes)));
        abortCheck(signal); onProgress(Math.round(Math.min(start + FINGERPRINT_BLOCK, file.size) * 100 / file.size));
    }
    const manifest = new Uint8Array(header.length + hashes.length * 32); manifest.set(header);
    hashes.forEach((hash, i) => manifest.set(hash, header.length + i * 32));
    return [...new Uint8Array(await window.crypto.subtle.digest("SHA-256", manifest))].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function readVideoMetadata(file, signal) {
    fileType(file); abortCheck(signal);
    return new Promise((resolve, reject) => {
        const video = document.createElement("video"), url = URL.createObjectURL(file);
        const cleanup = () => { clearTimeout(timer); signal?.removeEventListener("abort", cancel); video.onloadedmetadata = null; video.onerror = null; video.removeAttribute("src"); video.load(); URL.revokeObjectURL(url); };
        const cancel = () => { cleanup(); reject(new DOMException("أُوقف فحص الفيديو.", "AbortError")); };
        const timer = setTimeout(() => { cleanup(); reject(new Error("تعذر قراءة الفيديو؛ جرّب ملفًا مدعومًا.")); }, 15000);
        signal?.addEventListener("abort", cancel, { once: true });
        video.onloadedmetadata = () => {
            const result = { duration_seconds: Math.round(video.duration * 1000) / 1000, width: video.videoWidth, height: video.videoHeight };
            cleanup();
            if (!Number.isFinite(result.duration_seconds) || result.duration_seconds < 3 || result.duration_seconds > 600 || result.width < 360 || result.height < 360 || result.width > 4096 || result.height > 4096) reject(new Error("الفيديو يحتاج مدة 3–600 ثانية وأبعادًا بين 360 و4096 بكسل."));
            else resolve(result);
        };
        video.onerror = () => { cleanup(); reject(new Error("تعذر تشغيل هذا الفيديو على جهازك؛ اختر ملفًا مدعومًا.")); };
        video.preload = "metadata"; video.src = url;
    });
}

export async function describeFile(file, options = {}) {
    const mime_type = fileType(file);
    const metadata = await readVideoMetadata(file, options.signal);
    const file_manifest_sha256 = await fingerprintFile(file, options);
    return { file_name: file.name, file_size: file.size, mime_type, file_manifest_sha256, ...metadata };
}

export function validateDestination(raw) {
    let url;
    try { url = new URL(raw); } catch { throw new Error("لم تثبت وجهة رفع TikTok."); }
    if (typeof raw !== "string" || raw.length > 8192 || /[\s\\]/.test(raw) || url.protocol !== "https:" || !HOSTS.has(url.hostname) || url.host !== url.hostname || url.username || url.password || url.hash || !["/video/", "/upload/"].includes(url.pathname) || !url.search) throw new Error("لم تثبت وجهة رفع TikTok.");
    // URL normalizes an explicit :443; reject it from the original authority.
    if (raw.split("/")[2]?.includes(":")) throw new Error("لم تثبت وجهة رفع TikTok.");
    return raw;
}

export async function uploadVideoFile(file, source, transfer, { signal, onProgress = () => {} } = {}) {
    const mime = fileType(file), expected = chunkPlan(file.size), destination = validateDestination(transfer?.url);
    if (!source || Object.keys(expected).some((key) => source[key] !== expected[key])) throw new Error("خطة الرفع لا تطابق الملف المختار.");
    const offset = transfer.offset ?? 0, expires = Date.parse(transfer.expires_at);
    if (!Number.isSafeInteger(offset) || offset < 0 || offset > file.size || offset % source.chunk_size || !Number.isFinite(expires)) throw new Error("لم تثبت نقطة استكمال الرفع؛ حدّث الحالة.");
    for (let index = offset / source.chunk_size; index < source.total_chunk_count; index += 1) {
        abortCheck(signal);
        if (expires <= Date.now()) throw new Error("انتهت مهلة الرفع؛ راجع العملية قبل إنشاء أي طلب جديد.");
        const start = index * source.chunk_size, end = index === source.total_chunk_count - 1 ? file.size : start + source.chunk_size;
        const controller = new AbortController(), cancel = () => controller.abort();
        signal?.addEventListener("abort", cancel, { once: true });
        const timer = setTimeout(cancel, 90000);
        try {
            const response = await fetch(destination, { method: "PUT", mode: "cors", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer", cache: "no-store", signal: controller.signal,
                headers: { "Content-Type": mime, "Content-Range": `bytes ${start}-${end - 1}/${file.size}` }, body: file.slice(start, end, mime) });
            const expectedStatus = index === source.total_chunk_count - 1 ? 201 : 206;
            if (response.status !== expectedStatus) throw new Error("unproven chunk");
            onProgress(Math.round(end * 100 / file.size));
        } catch {
            throw new Error("تعذر تأكيد نقل جزء الفيديو إلى TikTok. حدّث حالة العملية قبل الاستكمال؛ لم يُكرر الإرسال.");
        } finally { clearTimeout(timer); signal?.removeEventListener("abort", cancel); }
    }
}
