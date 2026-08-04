import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowClockwise,
    Barcode,
    Buildings,
    Camera,
    CheckCircle,
    ClockCounterClockwise,
    Package,
    SpinnerGap,
    UserCircle,
    WarningCircle,
} from "@phosphor-icons/react";

import {
    closeSupplierReceivingSession,
    loadSupplierReceivingCatalog,
    newSupplierReceivingRequestId,
    openSupplierReceivingSession,
    scanSupplierReceivingPiece,
} from "../../services/supplierReceiving";

const PREPARATION_TRACKS = [
    "من المستودع",
    "من المورد",
    "تصنيع داخلي",
    "ينتظر توريد",
    "قيد التجميع",
    "متوقف بسبب نقص منتج",
];

export function formatReceivingDate(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("ar-SA", {
        timeZone: "Asia/Riyadh",
        dateStyle: "medium",
        timeStyle: "short",
    }).format(parsed);
}
export function supplierDisplayName(session) {
    return session?.supplier?.company_name || "مورد غير محدد";
}

function EmptyImage() {
    return (
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-400">
            <Package size={24} weight="duotone" />
        </div>
    );
}

function ScanRow({ scan }) {
    return (
        <article className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-3 sm:grid-cols-[64px_minmax(0,1fr)_auto] sm:items-center" data-testid="supplier-receiving-scan-row">
            {scan.selected_image_url ? (
                <img src={scan.selected_image_url} alt="" className="h-14 w-14 rounded-xl border border-slate-200 object-cover" />
            ) : <EmptyImage />}
            <div className="min-w-0">
                <div className="truncate font-black text-slate-950">{scan.product_name || "منتج"}</div>
                <div className="mt-1 text-xs font-bold text-slate-500">
                    طلب {scan.order_number || "—"} · ملف {scan.file_number || "—"} · قطعة {scan.unit_index || "—"}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-[11px] font-extrabold">
                    <span className="rounded-full bg-violet-50 px-2 py-1 text-violet-800">جهّزها: {scan.preparation_employee_name || "—"}</span>
                    <span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-800">استلمها: {scan.receiving_employee_name || "—"}</span>
                </div>
            </div>
            <div className="text-xs font-bold text-slate-500 sm:text-left">{formatReceivingDate(scan.occurred_at)}</div>
        </article>
    );
}

export function SupplierPieceCameraScanner({ onDetected, onClose }) {
    const videoRef = useRef(null);
    const [cameraError, setCameraError] = useState("");
    const [cameraReady, setCameraReady] = useState(false);

    useEffect(() => {
        let stopped = false;
        let detected = false;
        let stream;
        let animationFrame;

        async function startCamera() {
            if (!navigator.mediaDevices?.getUserMedia || !globalThis.BarcodeDetector) {
                setCameraError("هذا المتصفح لا يدعم قراءة QR بالكاميرا. افتح ميزان من Chrome أو Edge على الجوال، أو استخدم قارئ الباركود الخارجي.");
                return;
            }

            try {
                const getSupportedFormats = globalThis.BarcodeDetector.getSupportedFormats;
                const supported = typeof getSupportedFormats === "function"
                    ? await getSupportedFormats.call(globalThis.BarcodeDetector)
                    : [];
                const formats = ["qr_code", "code_128"].filter((value) => supported.includes(value));
                const detector = new globalThis.BarcodeDetector(formats.length ? { formats } : undefined);
                stream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: { ideal: "environment" },
                        width: { ideal: 1280 },
                        height: { ideal: 720 },
                    },
                    audio: false,
                });

                if (stopped || !videoRef.current) {
                    for (const track of stream?.getTracks?.() || []) track.stop();
                    return;
                }

                videoRef.current.srcObject = stream;
                await videoRef.current.play();
                if (stopped) return;
                setCameraReady(true);

                const detectFrame = async () => {
                    if (stopped || detected || !videoRef.current) return;
                    try {
                        const rows = await detector.detect(videoRef.current);
                        const value = String(rows?.[0]?.rawValue || "").trim();
                        if (value) {
                            detected = true;
                            onDetected(value);
                            return;
                        }
                    } catch {
                        // A frame without a readable QR is expected while the camera is moving.
                    }
                    animationFrame = requestAnimationFrame(detectFrame);
                };

                animationFrame = requestAnimationFrame(detectFrame);
            } catch (cameraStartError) {
                const messages = {
                    NotAllowedError: "اسمح لميزان باستخدام الكاميرا من إعدادات المتصفح ثم حاول مرة أخرى.",
                    NotFoundError: "لم يتم العثور على كاميرا في هذا الجهاز.",
                    NotReadableError: "الكاميرا مستخدمة في تطبيق آخر. أغلقه ثم حاول مرة أخرى.",
                    SecurityError: "تشغيل الكاميرا يحتاج فتح ميزان عبر اتصال آمن HTTPS.",
                };
                setCameraError(messages[cameraStartError?.name] || "تعذّر تشغيل الكاميرا. استخدم قارئ الباركود الخارجي أو الإدخال اليدوي.");
            }
        }

        startCamera();
        return () => {
            stopped = true;
            if (animationFrame) cancelAnimationFrame(animationFrame);
            for (const track of stream?.getTracks?.() || []) track.stop();
        };
    }, [onDetected]);

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/90 p-3" dir="rtl" role="dialog" aria-modal="true" aria-label="تصوير QR القطعة" data-testid="supplier-receiving-camera-dialog">
            <div className="w-full max-w-xl overflow-hidden rounded-3xl bg-white shadow-2xl">
                <div className="flex items-start justify-between gap-3 border-b border-slate-200 p-4">
                    <div>
                        <h3 className="flex items-center gap-2 text-lg font-black text-slate-950"><Camera size={24} className="text-emerald-700" weight="duotone" /> تصوير QR القطعة</h3>
                        <p className="mt-1 text-xs font-bold leading-5 text-slate-500">وجّه الكاميرا الخلفية إلى QR الموجود في بطاقة القطعة؛ سيستلمها ميزان تلقائيًا بعد القراءة.</p>
                    </div>
                    <button type="button" onClick={onClose} className="shrink-0 rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-black text-slate-800">إغلاق</button>
                </div>

                <div className="p-4">
                    {cameraError ? (
                        <div className="flex items-start gap-2 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm font-black leading-6 text-amber-950">
                            <WarningCircle size={22} className="mt-0.5 shrink-0" weight="fill" />
                            <span>{cameraError}</span>
                        </div>
                    ) : (
                        <div className="relative overflow-hidden rounded-2xl bg-black">
                            <video ref={videoRef} muted playsInline className="aspect-[3/4] max-h-[70vh] w-full object-cover sm:aspect-[4/3]" />
                            {!cameraReady && (
                                <div className="absolute inset-0 flex items-center justify-center bg-slate-950 text-sm font-black text-white">
                                    <SpinnerGap size={24} className="ml-2 animate-spin" /> جارٍ تشغيل الكاميرا…
                                </div>
                            )}
                            {cameraReady && (
                                <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-10">
                                    <div className="aspect-square w-full max-w-72 rounded-3xl border-4 border-emerald-400 shadow-[0_0_0_999px_rgba(2,6,23,0.35)]" />
                                </div>
                            )}
                        </div>
                    )}
                    <p className="mt-3 text-center text-xs font-bold text-slate-600">ثبّت QR داخل الإطار الأخضر وقرّب الكاميرا حتى تصبح الصورة واضحة.</p>
                </div>
            </div>
        </div>
    );
}

export default function SupplierReceivingWorkspace() {
    const [data, setData] = useState({ suppliers: [], sessions: [], active_session_scans: [] });
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");
    const [supplierId, setSupplierId] = useState("");
    const [openNote, setOpenNote] = useState("");
    const [closeNote, setCloseNote] = useState("");
    const [barcode, setBarcode] = useState("");
    const [cameraOpen, setCameraOpen] = useState(false);
    const [error, setError] = useState("");
    const [lastScan, setLastScan] = useState(null);
    const barcodeRef = useRef(null);

    const load = useCallback(async ({ quiet = false } = {}) => {
        if (!quiet) setLoading(true);
        setError("");
        try {
            const result = await loadSupplierReceivingCatalog({ limit: 50 });
            setData(result || {});
        } catch (loadError) {
            setError(loadError.message || "تعذّر تحميل جلسات الاستلام.");
        } finally {
            if (!quiet) setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);
    const active = data?.active_session || null;
    const scans = Array.isArray(data?.active_session_scans) ? data.active_session_scans : [];
    const closedSessions = useMemo(
        () => (data?.sessions || []).filter((row) => row?.status === "closed"),
        [data?.sessions],
    );

    useEffect(() => {
        if (active && !busy) barcodeRef.current?.focus();
    }, [active, busy]);

    async function openSession(event) {
        event.preventDefault();
        if (!supplierId || busy) return;
        setBusy("open");
        setError("");
        try {
            const result = await openSupplierReceivingSession({
                client_request_id: newSupplierReceivingRequestId(),
                supplier_id: supplierId,
                note: openNote.trim() || null,
            });
            setData((current) => ({
                ...current,
                active_session: result.session,
                active_session_scans: [],
                sessions: [result.session, ...(current.sessions || [])],
            }));
            setOpenNote("");
            setLastScan(null);
        } catch (openError) {
            setError(openError.message);
        } finally {
            setBusy("");
        }
    }

    const receivePiece = useCallback(async (rawValue, { refocus = true } = {}) => {
        const value = String(rawValue || "").trim();
        if (!active?.id || !value || busy) return;
        setBusy("scan");
        setError("");
        try {
            const result = await scanSupplierReceivingPiece(active.id, value);
            setLastScan(result.scan);
            setBarcode("");
            setData((current) => ({
                ...current,
                active_session: result.session,
                active_session_scans: [
                    result.scan,
                    ...(current.active_session_scans || []).filter(
                        (row) => row.piece_id !== result.scan?.piece_id,
                    ),
                ],
                sessions: (current.sessions || []).map((row) => (
                    row.id === result.session?.id ? result.session : row
                )),
            }));
        } catch (scanError) {
            setError(scanError.message);
            setBarcode("");
        } finally {
            setBusy("");
            if (refocus) window.setTimeout(() => barcodeRef.current?.focus(), 0);
        }
    }, [active?.id, busy]);

    function scanPiece(event) {
        event.preventDefault();
        receivePiece(barcode);
    }

    const handleCameraDetected = useCallback((value) => {
        setCameraOpen(false);
        setBarcode(value);
        receivePiece(value, { refocus: false });
    }, [receivePiece]);

    async function closeSession() {
        if (!active?.id || busy) return;
        setBusy("close");
        setError("");
        try {
            await closeSupplierReceivingSession(active.id, closeNote.trim());
            setCloseNote("");
            setLastScan(null);
            await load({ quiet: true });
        } catch (closeError) {
            setError(closeError.message);
        } finally {
            setBusy("");
        }
    }

    return (
        <section className="space-y-5" dir="rtl" data-testid="supplier-receiving-workspace">
            <div className="overflow-hidden rounded-2xl border border-violet-200 bg-white shadow-sm">
                <header className="bg-gradient-to-l from-slate-950 to-violet-900 p-5 text-white sm:p-6">
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                        <div>
                            <div className="text-xs font-black text-violet-200">Supplier Receiving V1</div>
                            <h2 className="mt-1 text-2xl font-black">استلام منتجات المورد بالباركود</h2>
                            <p className="mt-2 max-w-3xl text-sm font-semibold leading-6 text-violet-100">
                                افتح جلسة للمورد، ثم امسح QR كل قطعة. يسجل ميزان موظف التجهيز وموظف الاستلام كلًا بشكل مستقل ويمنع استلام القطعة مرتين.
                            </p>
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                            <div className="rounded-2xl border border-white/15 bg-white/10 px-4 py-3 text-center">
                                <div className="text-2xl font-black tabular-nums">{Number(active?.scan_count || 0)}</div>
                                <div className="text-[11px] font-bold text-violet-100">داخل الجلسة</div>
                            </div>
                            <div className="rounded-2xl border border-white/15 bg-white/10 px-4 py-3 text-center">
                                <div className="text-2xl font-black tabular-nums">{Number(data?.eligible_piece_count || 0)}</div>
                                <div className="text-[11px] font-bold text-violet-100">قابلة للاستلام</div>
                            </div>
                        </div>
                    </div>
                </header>
                <div className="flex flex-wrap gap-2 border-t border-slate-100 bg-slate-50 p-3">
                    {PREPARATION_TRACKS.map((track) => (
                        <span key={track} className={`rounded-full border px-3 py-1.5 text-xs font-black ${track === "من المورد" ? "border-violet-500 bg-violet-100 text-violet-950" : "border-slate-200 bg-white text-slate-500"}`}>{track}</span>
                    ))}
                </div>
            </div>

            <div className="flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm font-bold leading-6 text-amber-950">
                <WarningCircle size={22} className="mt-0.5 shrink-0" weight="fill" />
                <div>هذه الجلسة تربط مورد ميزان 2 بالقطعة وخدماتها تشغيليًا فقط. لا تنشئ فاتورة أو مديونية ولا ترسل شيئًا إلى قيود أو سلة.</div>
            </div>

            {error && (
                <div className="flex items-start gap-2 rounded-2xl border border-rose-300 bg-rose-50 p-4 text-sm font-black text-rose-950" data-testid="supplier-receiving-error">
                    <WarningCircle size={21} className="mt-0.5 shrink-0" />{error}
                </div>
            )}

            {!active ? (
                <form onSubmit={openSession} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="supplier-receiving-open-form">
                    <div className="flex items-center gap-3">
                        <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-violet-100 text-violet-700"><Buildings size={24} weight="duotone" /></span>
                        <div><h3 className="font-black text-slate-950">فتح جلسة استلام جديدة</h3><p className="mt-1 text-xs font-bold text-slate-500">يمكن لكل موظف يملك صلاحية الاستلام فتح جلسته الخاصة.</p></div>
                    </div>
                    <div className="mt-5 grid gap-3 lg:grid-cols-[minmax(220px,1fr)_minmax(260px,2fr)_auto]">
                        <select value={supplierId} onChange={(event) => setSupplierId(event.target.value)} disabled={loading || busy === "open"} className="min-h-12 rounded-xl border border-slate-200 bg-white px-3 text-sm font-black outline-none focus:border-violet-500" aria-label="المورد">
                            <option value="">اختر المورد</option>
                            {(data?.suppliers || []).map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.company_name} · {supplier.service_links?.map((service) => service.service_name).filter(Boolean).join("، ") || "بلا خدمات"}</option>)}
                        </select>
                        <input value={openNote} onChange={(event) => setOpenNote(event.target.value)} placeholder="ملاحظة للجلسة — اختياري" className="min-h-12 rounded-xl border border-slate-200 px-3 text-sm font-bold outline-none focus:border-violet-500" />
                        <button type="submit" disabled={!supplierId || loading || busy === "open"} className="min-h-12 rounded-xl bg-violet-700 px-5 text-sm font-black text-white disabled:opacity-50">
                            {busy === "open" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <Barcode className="ml-1 inline" />} فتح الجلسة
                        </button>
                    </div>
                    {loading && <div className="mt-4 text-xs font-bold text-violet-700"><SpinnerGap className="ml-1 inline animate-spin" /> جارٍ تحميل الموردين والجلسات…</div>}
                    {!loading && !(data?.suppliers || []).length && <div className="mt-4 rounded-xl border border-dashed border-amber-300 bg-amber-50 p-3 text-xs font-bold text-amber-900">لا يوجد مورد نشط مرتبط بخدمات في ميزان 2. <Link to="/suppliers-v2" className="underline">افتح صفحة الموردين لإضافة المورد وخدماته.</Link></div>}
                </form>
            ) : (
                <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.75fr)]">
                    <div className="space-y-4">
                        <section className="rounded-2xl border border-emerald-300 bg-emerald-50 p-4 shadow-sm" data-testid="supplier-receiving-active-session">
                            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                <div>
                                    <div className="flex flex-wrap items-center gap-2"><span className="rounded-full bg-emerald-700 px-2.5 py-1 text-xs font-black text-white">جلسة مفتوحة</span><span className="font-mono text-xs font-bold text-emerald-900">{active.reference}</span></div>
                                    <h3 className="mt-2 text-xl font-black text-emerald-950">{supplierDisplayName(active)}</h3>
                                    <p className="mt-1 text-xs font-bold text-emerald-800">فتحها: {active.opened_by_name || "—"} · {formatReceivingDate(active.opened_at)}</p>
                                </div>
                                <button type="button" onClick={() => load()} disabled={loading || !!busy} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-emerald-300 bg-white px-3 text-xs font-black text-emerald-900 disabled:opacity-50"><ArrowClockwise size={17} className={loading ? "animate-spin" : ""} /> تحديث</button>
                            </div>
                            <form onSubmit={scanPiece} className="mt-5 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto]" data-testid="supplier-receiving-scan-form">
                                <label className="relative block">
                                    <Barcode size={22} className="absolute right-4 top-1/2 -translate-y-1/2 text-emerald-700" />
                                    <input ref={barcodeRef} value={barcode} onChange={(event) => setBarcode(event.target.value)} autoComplete="off" inputMode="text" placeholder="امسح QR القطعة هنا" disabled={busy === "scan"} className="min-h-14 w-full rounded-2xl border-2 border-emerald-300 bg-white pr-12 pl-4 font-mono text-base font-black outline-none focus:border-emerald-600 focus:ring-4 focus:ring-emerald-100" data-testid="supplier-receiving-barcode-input" />
                                </label>
                                <button type="button" onClick={() => setCameraOpen(true)} disabled={busy === "scan"} className="inline-flex min-h-14 items-center justify-center gap-2 rounded-2xl border-2 border-emerald-600 bg-white px-5 text-base font-black text-emerald-800 disabled:opacity-50" data-testid="supplier-receiving-camera-button">
                                    <Camera size={22} weight="duotone" /> فتح الكاميرا
                                </button>
                                <button type="submit" disabled={!barcode.trim() || busy === "scan"} className="min-h-14 rounded-2xl bg-emerald-700 px-7 text-base font-black text-white disabled:opacity-50">
                                    {busy === "scan" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" weight="fill" />} استلام القطعة
                                </button>
                            </form>
                            <p className="mt-3 text-xs font-bold leading-5 text-emerald-800">من الجوال اضغط «فتح الكاميرا»، أو استخدم قارئ الباركود مثل لوحة المفاتيح واضغط Enter. ملفات التجهيز المعاد تنزيلها تحمل QR فريدًا لكل قطعة.</p>
                        </section>

                        {lastScan && (
                            <div className="rounded-2xl border-2 border-emerald-400 bg-white p-4 shadow-sm" data-testid="supplier-receiving-last-success">
                                <div className="mb-3 flex items-center gap-2 font-black text-emerald-800"><CheckCircle size={24} weight="fill" /> تم استلام القطعة ومنع تكرارها</div>
                                <ScanRow scan={lastScan} />
                            </div>
                        )}

                        <section className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                            <div className="mb-3 flex items-center justify-between gap-3"><div><h3 className="font-black text-slate-950">قطع الجلسة</h3><p className="mt-1 text-xs font-bold text-slate-500">آخر القطع أولًا — إجمالي {Number(active.scan_count || 0)}</p></div><Barcode size={25} className="text-violet-700" /></div>
                            {!scans.length ? <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center text-sm font-bold text-slate-500">لم تُمسح أي قطعة بعد.</div> : <div className="space-y-2">{scans.map((scan) => <ScanRow key={scan.piece_id} scan={scan} />)}</div>}
                        </section>
                    </div>

                    <aside className="space-y-4">
                        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                            <div className="flex items-center gap-2 font-black text-slate-950"><UserCircle size={23} className="text-violet-700" /> سجل المسؤوليات</div>
                            <div className="mt-4 space-y-3 text-sm font-bold">
                                <div className="rounded-xl bg-violet-50 p-3 text-violet-900">موظف التجهيز محفوظ أصلًا مع كل قطعة ولا يتغير بالمسح.</div>
                                <div className="rounded-xl bg-emerald-50 p-3 text-emerald-900">موظف الاستلام هو صاحب هذه الجلسة ويسجل مستقلًا.</div>
                            </div>
                        </section>
                        <section className="rounded-2xl border border-rose-200 bg-white p-4 shadow-sm">
                            <h3 className="font-black text-slate-950">إغلاق الجلسة</h3>
                            <p className="mt-1 text-xs font-bold leading-5 text-slate-500">الإغلاق يثبت العدد وسجل التدقيق. لا ينشئ فاتورة المورد.</p>
                            <textarea value={closeNote} onChange={(event) => setCloseNote(event.target.value)} rows={3} placeholder="ملاحظة الإغلاق — اختياري" className="mt-3 w-full rounded-xl border border-slate-200 p-3 text-sm font-bold outline-none focus:border-rose-400" />
                            <button type="button" onClick={closeSession} disabled={busy === "close"} className="mt-3 min-h-11 w-full rounded-xl bg-slate-950 px-4 text-sm font-black text-white disabled:opacity-50">{busy === "close" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" />} إغلاق وتثبيت الجلسة</button>
                        </section>
                    </aside>
                </div>
            )}

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="supplier-receiving-history">
                <div className="flex items-center gap-2"><ClockCounterClockwise size={23} className="text-violet-700" /><div><h3 className="font-black text-slate-950">سجل جلسات الاستلام المغلقة</h3><p className="mt-1 text-xs font-bold text-slate-500">مرجع تشغيلي محفوظ؛ لا تُحذف الجلسات أو القطع.</p></div></div>
                {!closedSessions.length ? <div className="mt-4 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center text-sm font-bold text-slate-500">لا توجد جلسات مغلقة بعد.</div> : <div className="mt-4 grid gap-3 lg:grid-cols-2">{closedSessions.map((session) => (
                    <article key={session.id} className="rounded-2xl border border-slate-200 p-4">
                        <div className="flex items-start justify-between gap-3"><div><div className="font-black text-slate-950">{supplierDisplayName(session)}</div><div className="mt-1 font-mono text-xs font-bold text-violet-700">{session.reference}</div></div><span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-black text-slate-700">{session.scan_count} قطعة</span></div>
                        <div className="mt-3 text-xs font-bold text-slate-500">أغلقها {session.closed_by_name || session.opened_by_name || "—"} · {formatReceivingDate(session.closed_at)}</div>
                    </article>
                ))}</div>}
            </section>

            {cameraOpen && active && (
                <SupplierPieceCameraScanner
                    onDetected={handleCameraDetected}
                    onClose={() => setCameraOpen(false)}
                />
            )}
        </section>
    );
}
