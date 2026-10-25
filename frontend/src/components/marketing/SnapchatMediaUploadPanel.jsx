import { useCallback, useEffect, useRef, useState } from "react";
import {
    createSnapchatMediaUploadPreview, getSnapchatMediaProfiles, prepareSnapchatVideoFile,
    reconcileSnapchatMediaUpload, snapchatMediaUploadError, uploadSnapchatVideoFile,
} from "../../services/snapchatMediaUpload";

const LABELS = {
    idle: "اختر فيديو الإعلان.", hashing: "جارٍ فحص الملف…", prepared: "الفيديو جاهز للرفع.",
    previewing: "جارٍ تجهيز الرفع…", uploading: "جارٍ رفع الفيديو…",
    processing: "الفيديو قيد المعالجة لدى سناب. افحص الحالة لتحديثه.",
    uncertain: "لم تتأكد نتيجة الرفع. افحص الحالة قبل المحاولة مجددًا.",
    unresolved: "لم يتأكد إنشاء الفيديو لدى سناب. الرفع متوقف لمنع التكرار.",
    reconciling: "جارٍ فحص حالة الفيديو…", ready: "الفيديو جاهز للاستخدام في الإعلان.",
};

export default function SnapchatMediaUploadPanel({
    ownerId, accountId, accountName = "", allowed = false, disabled = false,
    profileId = "", onProfileChange, onReady, onClear,
}) {
    const [file, setFile] = useState(null);
    const [metadata, setMetadata] = useState(null);
    const [job, setJob] = useState(null);
    const [phase, setPhase] = useState("idle");
    const [busy, setBusy] = useState(false);
    const [progress, setProgress] = useState(0);
    const [aiGenerated, setAiGenerated] = useState(false);
    const [error, setError] = useState("");
    const [profiles, setProfiles] = useState([]);
    const [profilesLoading, setProfilesLoading] = useState(false);
    const [profileError, setProfileError] = useState("");
    const epoch = useRef(0);
    const profileEpoch = useRef(0);
    const busyRef = useRef(false);
    const request = useRef(null);
    const profileRequest = useRef(null);
    const callbacks = useRef({ onReady, onClear, onProfileChange });
    callbacks.current = { onReady, onClear, onProfileChange };
    const currentProfile = useRef(profileId);
    currentProfile.current = profileId;
    const scope = JSON.stringify([String(ownerId || ""), String(accountId || "")]);
    const currentScope = useRef(scope);
    currentScope.current = scope;
    const context = () => ({ ownerId: String(ownerId || ""), accountId: String(accountId || "") });
    const current = (generation, expectedScope) => (
        epoch.current === generation && currentScope.current === expectedScope
    );

    useEffect(() => {
        epoch.current += 1;
        request.current?.abort();
        busyRef.current = false;
        setBusy(false); setFile(null); setMetadata(null); setJob(null);
        setPhase("idle"); setProgress(0); setAiGenerated(false); setError("");
        callbacks.current.onClear?.({ ownerId: String(ownerId || ""), accountId: String(accountId || "") });
        return () => { epoch.current += 1; request.current?.abort(); };
    }, [ownerId, accountId]);

    const loadProfiles = useCallback(async () => {
        const generation = ++profileEpoch.current;
        const expectedScope = JSON.stringify([String(ownerId || ""), String(accountId || "")]);
        profileRequest.current?.abort();
        const controller = new AbortController();
        profileRequest.current = controller;
        setProfiles([]); setProfileError("");
        if (!ownerId || !accountId || !allowed) {
            setProfilesLoading(false);
            return;
        }
        setProfilesLoading(true);
        try {
            const next = await getSnapchatMediaProfiles({ accountId, signal: controller.signal });
            if (profileEpoch.current !== generation || currentScope.current !== expectedScope) return;
            setProfiles(next);
            const selected = next.some((item) => item.profile_id === currentProfile.current)
                ? currentProfile.current : next.length === 1 ? next[0].profile_id : "";
            callbacks.current.onProfileChange?.(selected, {
                ownerId: String(ownerId), accountId: String(accountId),
            });
            if (!next.length) setProfileError("لا يوجد ملف سناب متاح لهذا الحساب.");
        } catch (problem) {
            if (profileEpoch.current !== generation || currentScope.current !== expectedScope) return;
            callbacks.current.onProfileChange?.("", {
                ownerId: String(ownerId), accountId: String(accountId),
            });
            setProfileError(snapchatMediaUploadError(problem, "تعذّر جلب ملفات سناب المتاحة لهذا الحساب."));
        } finally {
            if (profileEpoch.current === generation && currentScope.current === expectedScope) setProfilesLoading(false);
        }
    }, [ownerId, accountId, allowed]);

    useEffect(() => {
        loadProfiles();
        return () => { profileEpoch.current += 1; profileRequest.current?.abort(); };
    }, [loadProfiles]);

    function clearProof() { callbacks.current.onClear?.(context()); }

    function adopt(nextJob, expectedMetadata, capturedContext) {
        if (nextJob.account_id !== capturedContext.accountId || !expectedMetadata
            || nextJob.sha256 !== expectedMetadata.sha256
            || nextJob.size_bytes !== expectedMetadata.size_bytes
            || nextJob.content_type !== expectedMetadata.content_type) {
            const problem = new Error("scope");
            problem.code = "snapchat_media_response_scope_invalid";
            throw problem;
        }
        setJob(nextJob);
        if (nextJob.ready_for_creative === true && nextJob.status === "ready"
            && nextJob.provider_media_status === "READY" && nextJob.provider_upload_attempted === true
            && nextJob.provider_media_id) {
            setPhase("ready"); setProgress(100);
            callbacks.current.onReady?.(nextJob, capturedContext);
        } else if (nextJob.status === "create_unknown") setPhase("unresolved");
        else if (nextJob.can_upload === true) setPhase("prepared");
        else setPhase("processing");
    }

    async function selectFile(event) {
        const selected = event.target.files?.[0] || null;
        const generation = ++epoch.current;
        const expectedScope = scope;
        request.current?.abort();
        const controller = new AbortController();
        request.current = controller;
        clearProof();
        setFile(selected); setMetadata(null); setJob(null); setProgress(0); setError("");
        if (!selected) {
            setPhase("idle"); busyRef.current = false; setBusy(false); return;
        }
        busyRef.current = true; setBusy(true); setPhase("hashing");
        try {
            const prepared = await prepareSnapchatVideoFile(selected, { signal: controller.signal });
            if (!current(generation, expectedScope)) return;
            setMetadata(prepared); setPhase("prepared");
        } catch (problem) {
            if (!current(generation, expectedScope)) return;
            setFile(null); setMetadata(null); setPhase("idle");
            setError(snapchatMediaUploadError(problem));
        } finally {
            if (current(generation, expectedScope)) { busyRef.current = false; setBusy(false); }
        }
    }

    async function startUpload() {
        if (busyRef.current || !ownerId || !accountId || !allowed || disabled
            || !file || !metadata || phase !== "prepared") return;
        busyRef.current = true; setBusy(true); clearProof(); setError(""); setProgress(0); setPhase("previewing");
        const generation = epoch.current;
        const expectedScope = scope;
        const capturedContext = context();
        const controller = new AbortController();
        request.current = controller;
        let receivedPreview = null;
        try {
            receivedPreview = await createSnapchatMediaUploadPreview({
                accountId, metadata, aiGenerated, signal: controller.signal,
            });
            if (!current(generation, expectedScope)) return;
            setJob(receivedPreview);
            if (!receivedPreview.can_upload || receivedPreview.ready_for_creative) {
                adopt(receivedPreview, metadata, capturedContext); return;
            }
            if (!receivedPreview.confirm_token) {
                const problem = new Error("preview");
                problem.code = "snapchat_media_reconciliation_required";
                throw problem;
            }
            setPhase("uploading");
            const result = await uploadSnapchatVideoFile({
                accountId, file, metadata, preview: receivedPreview, signal: controller.signal,
                onProgress: (value) => { if (current(generation, expectedScope)) setProgress(value); },
            });
            if (current(generation, expectedScope)) adopt(result, metadata, capturedContext);
        } catch (problem) {
            if (!current(generation, expectedScope)) return;
            clearProof();
            setPhase(receivedPreview ? "uncertain" : "prepared");
            setError(snapchatMediaUploadError(problem, receivedPreview
                ? "تعذّر تأكيد الرفع. افحص حالة الفيديو قبل المحاولة مجددًا." : "تعذّر تجهيز الرفع. حاول مجددًا."));
        } finally {
            if (current(generation, expectedScope)) { busyRef.current = false; setBusy(false); }
        }
    }

    async function reconcile() {
        if (busyRef.current || !ownerId || !accountId || !job?.upload_id) return;
        busyRef.current = true; setBusy(true); clearProof(); setError(""); setPhase("reconciling");
        const generation = epoch.current;
        const expectedScope = scope;
        const capturedContext = context();
        const controller = new AbortController();
        request.current = controller;
        try {
            const result = await reconcileSnapchatMediaUpload({
                accountId, uploadId: job.upload_id, signal: controller.signal,
            });
            if (current(generation, expectedScope)) adopt(result, metadata, capturedContext);
        } catch (problem) {
            if (!current(generation, expectedScope)) return;
            setPhase("uncertain"); setError(snapchatMediaUploadError(problem, "تعذّر فحص حالة الفيديو."));
        } finally {
            if (current(generation, expectedScope)) { busyRef.current = false; setBusy(false); }
        }
    }

    const unavailable = !ownerId || !accountId || !allowed || disabled;
    return (
        <section className="rounded-xl border border-sky-200 bg-sky-50/50 p-3 md:col-span-2" data-testid="snapchat-media-upload-panel">
            <h3 className="text-sm font-black text-slate-900">فيديو الإعلان</h3>
            {accountName && <p className="mt-1 text-xs text-slate-600">{accountName}</p>}
            <p className="mt-1 text-xs text-slate-600">MP4 أو MOV، حتى 32 ميجابايت. يُستخدم بعد تأكيد سناب.</p>
            <label className="mt-3 block text-xs font-bold text-slate-700">
                اختر فيديو من جهازك
                <input key={scope} type="file" accept=".mp4,.mov,video/mp4,video/quicktime"
                    disabled={unavailable || busy} onChange={selectFile}
                    data-testid="snapchat-media-file" className="mt-1 block w-full text-xs" />
            </label>
            {file && <p className="mt-2 break-all text-xs text-slate-700">{file.name}</p>}
            <label className="mt-3 flex items-start gap-2 text-xs text-slate-700">
                <input type="checkbox" checked={aiGenerated} disabled={unavailable || busy || Boolean(job)}
                    onChange={(event) => setAiGenerated(event.target.checked)} data-testid="snapchat-media-ai-generated" />
                الفيديو مولّد أو معدل بالذكاء الاصطناعي
            </label>
            <p className="mt-3 text-xs font-bold text-slate-800" role="status">
                {phase === "uploading" && progress === 100 ? "اكتمل إرسال الملف، جارٍ تأكيد حالته لدى سناب…" : LABELS[phase]}
            </p>
            {(phase === "uploading" || phase === "ready") && (
                <progress aria-label="تقدّم رفع الفيديو" value={progress} max={100} className="mt-2 h-2 w-full" />
            )}
            {unavailable && <p className="mt-2 text-xs text-slate-600">الرفع غير متاح لهذا الحساب حاليًا.</p>}
            {error && <p role="alert" className="mt-2 text-xs font-bold text-rose-700">{error}</p>}
            <div className="mt-3 flex flex-wrap gap-2">
                <button type="button" disabled={unavailable || busy || !metadata || phase !== "prepared"}
                    onClick={startUpload} data-testid="snapchat-media-upload"
                    className="min-h-10 rounded-lg bg-slate-900 px-4 text-xs font-bold text-white disabled:opacity-40">
                    رفع الفيديو إلى سناب
                </button>
                {job?.upload_id && <button type="button" disabled={busy || !ownerId || !accountId || disabled}
                    onClick={reconcile} data-testid="snapchat-media-reconcile"
                    className="min-h-10 rounded-lg border border-slate-300 bg-white px-4 text-xs font-bold text-slate-800 disabled:opacity-40">
                    فحص حالة الفيديو
                </button>}
            </div>
            <div className="mt-4 border-t border-sky-200 pt-3">
                <label className="block text-xs font-bold text-slate-700">
                    ملف سناب للإعلان
                    <select required value={profileId || ""} disabled={unavailable || busy || profilesLoading}
                        onChange={(event) => callbacks.current.onProfileChange?.(event.target.value, context())}
                        data-testid="snapchat-media-profile-select"
                        className="mt-1 min-h-10 w-full rounded-lg border border-slate-300 bg-white px-2">
                        <option value="">{profilesLoading ? "جارٍ جلب الملفات…" : "اختر ملف سناب"}</option>
                        {profiles.map((item) => <option key={item.profile_id} value={item.profile_id}>{item.display_name}</option>)}
                    </select>
                </label>
                {profileError && <p role="alert" className="mt-2 text-xs text-rose-700">{profileError}</p>}
                <button type="button" onClick={loadProfiles} disabled={unavailable || busy || profilesLoading}
                    data-testid="snapchat-media-profiles-refresh" className="mt-2 min-h-9 text-xs font-bold text-sky-800 disabled:opacity-40">
                    تحديث ملفات سناب
                </button>
            </div>
        </section>
    );
}
