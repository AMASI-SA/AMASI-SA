import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowLeft,
    Camera,
    CheckCircle,
    MagnifyingGlass,
    Package,
    SpinnerGap,
    UserCircle,
    WarningCircle,
    X,
} from "@phosphor-icons/react";

import {
    newPreparationReceiptRequestId,
    receivePreparationPiece,
    searchPreparationReceipt,
} from "../../services/preparationWorkService";

const BLOCKER_MESSAGES = {
    preparation_piece_already_received: "تم استلام المنتج",
    preparation_piece_cancelled: "المنتج ملغى",
    preparation_piece_stopped: "المنتج متوقف",
    preparation_piece_employee_required: "لم يُسند لموظف تجهيز",
    preparation_piece_supplier_receiving_in_progress: "داخل استلام المورد",
    preparation_piece_supplier_receipt_required: "يحتاج استلامه من المورد أولًا",
    preparation_piece_not_started: "لم يبدأ تجهيزه",
    preparation_piece_not_ready_for_receipt: "غير جاهز للاستلام",
};

export function CameraScanner({ onDetected, onClose }) {
    const videoRef = useRef(null);
    const [error, setError] = useState("");
    const [ready, setReady] = useState(false);

    useEffect(() => {
        let stopped = false;
        let detecting = false;
        let lastValue = "";
        let stream;
        let animationFrame;
        let zxingControls;

        const acceptValue = async (rawValue) => {
            const value = String(rawValue || "").trim();
            if (!value || value === lastValue || stopped || detecting) return;
            detecting = true;
            lastValue = value;
            try {
                await onDetected(value);
            } finally {
                detecting = false;
            }
        };

        const nativeDetector = async () => {
            if (!globalThis.BarcodeDetector) return null;
            try {
                const supported = typeof globalThis.BarcodeDetector.getSupportedFormats === "function"
                    ? await globalThis.BarcodeDetector.getSupportedFormats()
                    : [];
                const formats = ["qr_code", "code_128", "code_39"].filter(
                    (format) => !supported.length || supported.includes(format),
                );
                return new globalThis.BarcodeDetector(formats.length ? { formats } : undefined);
            } catch {
                return null;
            }
        };

        async function start() {
            if (!globalThis.navigator?.mediaDevices?.getUserMedia) {
                setError("الكاميرا غير متاحة. افتح ميزان عبر HTTPS واسمح للمتصفح باستخدام الكاميرا.");
                return;
            }
            try {
                stream = await globalThis.navigator.mediaDevices.getUserMedia({
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
                setReady(true);

                const detector = await nativeDetector();
                if (detector) {
                    const scanFrame = async () => {
                        if (stopped || !videoRef.current) return;
                        try {
                            const rows = await detector.detect(videoRef.current);
                            await acceptValue(rows?.[0]?.rawValue);
                        } catch {
                            lastValue = "";
                        }
                        animationFrame = globalThis.requestAnimationFrame(scanFrame);
                    };
                    animationFrame = globalThis.requestAnimationFrame(scanFrame);
                    return;
                }

                const { BarcodeFormat, BrowserMultiFormatReader } = await import("@zxing/browser");
                if (stopped || !videoRef.current) return;
                const reader = new BrowserMultiFormatReader(undefined, {
                    delayBetweenScanAttempts: 180,
                    delayBetweenScanSuccess: 500,
                });
                reader.possibleFormats = [
                    BarcodeFormat.QR_CODE,
                    BarcodeFormat.CODE_128,
                    BarcodeFormat.CODE_39,
                ];
                zxingControls = await reader.decodeFromVideoElement(
                    videoRef.current,
                    (result) => {
                        if (result) void acceptValue(result.getText());
                        else lastValue = "";
                    },
                );
            } catch (cameraError) {
                const messages = {
                    NotAllowedError: "اسمح لميزان باستخدام الكاميرا ثم حاول مرة أخرى.",
                    NotFoundError: "لم نجد كاميرا في هذا الجهاز.",
                    NotReadableError: "الكاميرا مستخدمة في تطبيق آخر.",
                    SecurityError: "تشغيل الكاميرا يحتاج اتصال HTTPS آمن.",
                };
                setError(messages[cameraError?.name] || "تعذّر تشغيل الكاميرا. يمكنك البحث برقم الطلب.");
            }
        }

        void start();
        return () => {
            stopped = true;
            if (animationFrame) globalThis.cancelAnimationFrame(animationFrame);
            zxingControls?.stop?.();
            for (const track of stream?.getTracks?.() || []) track.stop();
        };
    }, [onDetected]);

    return (
        <div className="fixed inset-0 z-[160] flex flex-col bg-slate-950" dir="rtl" role="dialog" aria-modal="true" aria-label="تصوير باركود المنتج" data-testid="preparation-receiving-camera">
            <header className="flex items-center justify-between gap-3 px-4 py-4 text-white">
                <div>
                    <div className="text-lg font-black">صوّر باركود المنتج</div>
                    <div className="text-xs font-bold text-slate-300">وجّه الكاميرا نحو المربع وسيتم استلام القطعة تلقائيًا</div>
                </div>
                <button type="button" onClick={onClose} className="flex h-11 w-11 items-center justify-center rounded-full bg-white/10" aria-label="إغلاق الكاميرا">
                    <X size={24} weight="bold" />
                </button>
            </header>
            <div className="relative min-h-0 flex-1 overflow-hidden bg-black">
                <video ref={videoRef} muted playsInline className="h-full w-full object-cover" />
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-slate-950/25 p-10">
                    <div className="aspect-square w-full max-w-sm rounded-[2rem] border-4 border-emerald-300 shadow-[0_0_0_999px_rgba(2,6,23,0.35)]" />
                </div>
                {!ready && !error && (
                    <div className="absolute inset-x-0 bottom-7 flex justify-center">
                        <span className="inline-flex items-center gap-2 rounded-full bg-slate-950/80 px-4 py-2 text-sm font-black text-white"><SpinnerGap className="animate-spin" />جاري تشغيل الكاميرا</span>
                    </div>
                )}
            </div>
            {error && (
                <div className="border-t border-rose-500/30 bg-rose-950 px-4 py-4 text-center text-sm font-black leading-6 text-rose-100">{error}</div>
            )}
        </div>
    );
}

function ProductImage({ piece }) {
    if (piece.image_url) {
        return <img src={piece.image_url} alt={piece.product_name || "منتج"} className="h-24 w-24 shrink-0 rounded-2xl border border-slate-200 object-cover sm:h-28 sm:w-28" />;
    }
    return (
        <div className="flex h-24 w-24 shrink-0 items-center justify-center rounded-2xl bg-slate-100 text-slate-400 sm:h-28 sm:w-28">
            <Package size={38} weight="duotone" />
        </div>
    );
}

export function ProductCard({ piece, busy, onReceive }) {
    const received = piece.status === "ready_for_assembly";
    return (
        <article className={`overflow-hidden rounded-3xl border-2 bg-white shadow-sm ${piece.search_match ? "border-violet-500 ring-4 ring-violet-100" : received ? "border-emerald-300" : "border-slate-200"}`} data-testid="preparation-receiving-product-card">
            {piece.search_match && (
                <div className="bg-violet-700 px-4 py-2 text-center text-xs font-black text-white">هذا هو المنتج الذي تم تصويره أو البحث عنه</div>
            )}
            <div className="p-4">
                <div className="flex items-start gap-3">
                    <ProductImage piece={piece} />
                    <div className="min-w-0 flex-1">
                        <div className="text-[10px] font-black text-slate-400">الطلب #{piece.order_number || "—"} · القطعة {piece.unit_index || "—"}</div>
                        <h3 className="mt-1 line-clamp-2 text-lg font-black leading-7 text-slate-950">{piece.product_name || "منتج"}</h3>
                        {piece.sku && <div className="mt-1 truncate text-xs font-bold text-slate-500">SKU: {piece.sku}</div>}
                        <div className="mt-2 inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-black text-slate-700">
                            <UserCircle size={15} weight="fill" /> جهّزه: {piece.responsible_employee_name || "—"}
                        </div>
                    </div>
                </div>

                <div className="mt-4 rounded-2xl bg-slate-50 p-3">
                    <div className="text-[11px] font-black text-slate-500">مواصفات العميل</div>
                    {piece.specifications?.length ? (
                        <div className="mt-2 grid grid-cols-2 gap-2">
                            {piece.specifications.map((spec, index) => (
                                <div key={`${spec.name}-${spec.value}-${index}`} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                                    <span className="block text-[10px] font-bold text-slate-400">{spec.name}</span>
                                    <span className="mt-0.5 block text-sm font-black text-slate-900">{spec.value}</span>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="mt-1 text-sm font-bold text-slate-600">لا توجد خيارات خاصة لهذا المنتج.</div>
                    )}
                </div>

                {piece.can_receive ? (
                    <button type="button" onClick={() => onReceive(piece)} disabled={busy} className="mt-4 inline-flex min-h-14 w-full items-center justify-center gap-2 rounded-2xl bg-emerald-700 px-4 text-base font-black text-white shadow-sm transition active:scale-[0.99] disabled:opacity-60" data-testid="receive-preparation-piece-button">
                        {busy ? <SpinnerGap size={23} className="animate-spin" /> : <CheckCircle size={24} weight="fill" />}
                        {busy ? "جاري الاستلام..." : "استلام المنتج جاهز"}
                    </button>
                ) : received ? (
                    <div className="mt-4 flex min-h-14 items-center justify-center gap-2 rounded-2xl bg-emerald-50 px-4 text-sm font-black text-emerald-800">
                        <CheckCircle size={24} weight="fill" /> تم الاستلام — جاهز للتجميع والعنونة
                    </div>
                ) : (
                    <div className="mt-4 flex min-h-14 items-center justify-center gap-2 rounded-2xl bg-amber-50 px-4 text-center text-sm font-black text-amber-900">
                        <WarningCircle size={22} weight="fill" /> {BLOCKER_MESSAGES[piece.blocker_code] || piece.status_label || "غير جاهز للاستلام"}
                    </div>
                )}
            </div>
        </article>
    );
}

export default function PreparationEmployeeReceivingWorkspace() {
    const [query, setQuery] = useState("");
    const [receivedPieces, setReceivedPieces] = useState([]);
    const [lastProgress, setLastProgress] = useState(null);
    const [searching, setSearching] = useState(false);
    const [cameraOpen, setCameraOpen] = useState(false);
    const [success, setSuccess] = useState("");
    const [popup, setPopup] = useState(null);
    const receivedThisSession = useRef(new Set());

    const showPopup = useCallback((title, message) => {
        setPopup({ title, message });
    }, []);

    const receiveScannedPiece = useCallback(async (rawQuery) => {
        const value = String(rawQuery ?? query).trim();
        if (!value) {
            showPopup(
                "باركود القطعة مطلوب",
                "افتح الكاميرا وصوّر باركود القطعة، أو أدخل الباركود يدويًا.",
            );
            return null;
        }

        setQuery(value);
        setSearching(true);
        setSuccess("");
        try {
            const searchResult = await searchPreparationReceipt(value);
            const matchedPieceId = String(searchResult.matched_piece_id || "").trim();
            if (!matchedPieceId) {
                showPopup(
                    "صوّر باركود قطعة واحدة",
                    "رقم الطلب لا يستلم المنتجات. يجب تصوير باركود كل قطعة منفردة.",
                );
                return null;
            }

            const matchedPiece = searchResult.pieces?.find(
                (piece) => piece.piece_id === matchedPieceId,
            );
            if (!matchedPiece) {
                showPopup(
                    "المنتج غير موجود",
                    "لم نجد القطعة المصوّرة ضمن منتجات أماسي.",
                );
                return null;
            }

            const alreadyReceived = (
                matchedPiece.status === "ready_for_assembly"
                || matchedPiece.blocker_code === "preparation_piece_already_received"
                || Boolean(matchedPiece.preparation_received_at)
            );
            if (alreadyReceived || receivedThisSession.current.has(matchedPieceId)) {
                const currentSession = receivedThisSession.current.has(matchedPieceId);
                showPopup(
                    currentSession
                        ? "تم استلام المنتج في هذه الجلسة"
                        : "تم استلام المنتج سابقًا",
                    currentSession
                        ? "هذه القطعة موجودة ضمن مستلمات الجلسة الحالية ولن تُسجل مرة أخرى."
                        : "هذه القطعة استُلمت في جلسة سابقة ولن تُسجل مرة أخرى.",
                );
                return null;
            }

            if (!matchedPiece.can_receive) {
                showPopup(
                    "تعذّر استلام المنتج",
                    BLOCKER_MESSAGES[matchedPiece.blocker_code]
                        || matchedPiece.status_label
                        || "حالة القطعة لا تسمح باستلامها الآن.",
                );
                return null;
            }

            const response = await receivePreparationPiece(
                matchedPieceId,
                newPreparationReceiptRequestId(),
            );
            if (response.idempotent) {
                showPopup(
                    "تم استلام المنتج سابقًا",
                    "هذه القطعة مسجلة كمستلمة ولن تُسجل مرة أخرى.",
                );
                return null;
            }

            receivedThisSession.current.add(matchedPieceId);
            const receivedPiece = {
                ...matchedPiece,
                ...(response.piece || {}),
                piece_id: matchedPieceId,
                status: "ready_for_assembly",
                can_receive: false,
                blocker_code: "preparation_piece_already_received",
                search_match: true,
            };
            setReceivedPieces((current) => [
                receivedPiece,
                ...current.filter((piece) => piece.piece_id !== matchedPieceId),
            ]);
            setLastProgress(response.progress || null);
            setQuery("");
            setSuccess(
                response.progress?.order_ready_for_assembly
                    ? "تم استلام القطعة. اكتمل الطلب #" + response.progress.order_number + " وانتقل إلى التجميع والعنونة."
                    : "تم استلام القطعة وخصمها من حساب موظف التجهيز.",
            );
            return response;
        } catch (receiveError) {
            showPopup(
                "تعذّر استلام المنتج",
                receiveError?.message || "تعذّر استلام القطعة من موظف التجهيز.",
            );
            return null;
        } finally {
            setSearching(false);
        }
    }, [query, showPopup]);

    const handleDetected = useCallback(async (value) => {
        setCameraOpen(false);
        await receiveScannedPiece(value);
    }, [receiveScannedPiece]);

    const completed = Boolean(lastProgress?.order_ready_for_assembly);

    return (
        <section className="mx-auto w-full max-w-3xl space-y-4" dir="rtl" data-testid="preparation-employee-receiving-workspace">
            <div className="overflow-hidden rounded-3xl border border-emerald-200 bg-white shadow-sm">
                <div className="bg-emerald-800 px-4 py-5 text-white sm:px-6">
                    <div className="text-xs font-black text-emerald-100">مرحلة الاستلام من موظف التجهيز</div>
                    <h2 className="mt-1 text-2xl font-black">استلام قطعة بقطعة</h2>
                    <p className="mt-2 text-sm font-bold leading-6 text-emerald-100">تصوير الباركود يستلم قطعة واحدة فورًا، ويخصمها من حساب موظف التجهيز، ويضيفها إلى مستلمات الجلسة.</p>
                </div>
                <div className="grid grid-cols-3 border-b border-slate-100 bg-slate-50 text-center text-[10px] font-black sm:text-xs">
                    <div className="px-2 py-3 text-slate-500"><CheckCircle className="mx-auto mb-1" size={18} weight="fill" />قيد التجهيز</div>
                    <div className="border-x border-emerald-200 bg-emerald-50 px-2 py-3 text-emerald-800"><Camera className="mx-auto mb-1" size={18} weight="fill" />استلام قطعة</div>
                    <div className="px-2 py-3 text-slate-500"><Package className="mx-auto mb-1" size={18} weight="duotone" />التجميع والعنونة</div>
                </div>
                <form onSubmit={(event) => { event.preventDefault(); void receiveScannedPiece(); }} className="p-4 sm:p-5">
                    <label htmlFor="preparation-receipt-search" className="mb-2 block text-sm font-black text-slate-900">أدخل باركود القطعة</label>
                    <div className="flex gap-2">
                        <div className="relative min-w-0 flex-1">
                            <MagnifyingGlass size={21} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" weight="bold" />
                            <input id="preparation-receipt-search" value={query} onChange={(event) => setQuery(event.target.value)} inputMode="search" autoComplete="off" placeholder="باركود قطعة أماسي" className="h-14 w-full rounded-2xl border-2 border-slate-200 bg-white pr-11 pl-3 text-base font-black text-slate-950 outline-none placeholder:text-slate-400 focus:border-emerald-500" />
                        </div>
                        <button type="button" onClick={() => { setSuccess(""); setCameraOpen(true); }} className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-violet-700 text-white shadow-sm" aria-label="فتح الكاميرا لتصوير باركود المنتج" data-testid="open-preparation-receiving-camera">
                            <Camera size={27} weight="fill" />
                        </button>
                        <button type="submit" disabled={searching} className="hidden h-14 items-center justify-center rounded-2xl bg-slate-950 px-5 text-sm font-black text-white disabled:opacity-50 sm:inline-flex">
                            {searching ? <SpinnerGap size={21} className="animate-spin" /> : "استلام"}
                        </button>
                    </div>
                    <button type="submit" disabled={searching} className="mt-2 inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl bg-slate-950 text-sm font-black text-white disabled:opacity-50 sm:hidden">
                        {searching ? <><SpinnerGap size={20} className="animate-spin" />جاري استلام القطعة</> : <><CheckCircle size={20} weight="fill" />استلام القطعة</>}
                    </button>
                </form>
            </div>

            {success && <div className="flex items-start gap-2 rounded-2xl border border-emerald-300 bg-emerald-50 p-4 text-sm font-black leading-6 text-emerald-900" role="status"><CheckCircle size={22} className="mt-0.5 shrink-0" weight="fill" />{success}</div>}

            <div className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm" data-testid="preparation-receiving-session-summary">
                <div>
                    <div className="text-[10px] font-black text-slate-400">مستلمات الجلسة</div>
                    <div className="text-xl font-black text-slate-950">{receivedPieces.length} قطعة</div>
                </div>
                <div className="rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-black text-emerald-800">كل باركود = قطعة واحدة</div>
            </div>

            {!receivedPieces.length && !searching && (
                <div className="rounded-3xl border-2 border-dashed border-slate-200 bg-white px-5 py-10 text-center">
                    <span className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-700"><Camera size={34} weight="duotone" /></span>
                    <h3 className="mt-4 text-lg font-black text-slate-950">ابدأ بتصوير باركود أول قطعة</h3>
                    <p className="mx-auto mt-2 max-w-sm text-sm font-bold leading-6 text-slate-500">لن تظهر بقية منتجات الطلب هنا. كل قطعة تُضاف فقط بعد تصوير باركودها المنفرد.</p>
                </div>
            )}

            {searching && (
                <div className="flex min-h-40 flex-col items-center justify-center gap-3 rounded-3xl border border-slate-200 bg-white text-emerald-800">
                    <SpinnerGap size={34} className="animate-spin" />
                    <span className="text-sm font-black">جاري استلام القطعة الواحدة</span>
                </div>
            )}

            {receivedPieces.length > 0 && !searching && (
                <div className="space-y-3" data-testid="preparation-receiving-results">
                    {completed && (
                        <div className="rounded-3xl border-2 border-emerald-400 bg-emerald-50 p-5 text-center" data-testid="preparation-order-ready-for-assembly">
                            <CheckCircle size={42} weight="fill" className="mx-auto text-emerald-700" />
                            <h3 className="mt-2 text-xl font-black text-emerald-950">اكتمل استلام الطلب #{lastProgress.order_number}</h3>
                            <p className="mt-1 text-sm font-bold leading-6 text-emerald-800">الآن فقط يظهر الطلب كاملًا داخل مرحلة التجميع والعنونة.</p>
                            <Link to="/fulfillment-v2?stage=ready_to_ship" className="mt-4 inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl bg-emerald-800 px-4 text-sm font-black text-white">فتح التجميع والعنونة <ArrowLeft size={20} weight="bold" /></Link>
                        </div>
                    )}

                    {receivedPieces.map((piece) => (
                        <ProductCard key={piece.piece_id} piece={piece} busy={false} onReceive={() => {}} />
                    ))}
                </div>
            )}

            <div className="rounded-2xl border border-violet-200 bg-violet-50 p-4 text-sm font-bold leading-6 text-violet-950">
                عرض الطلب كاملًا خاص بمرحلة <strong>التجميع والعنونة</strong>. هذه الصفحة تستلم كل قطعة بصورة مستقلة فقط.
            </div>

            {cameraOpen && <CameraScanner onDetected={handleDetected} onClose={() => setCameraOpen(false)} />}

            {popup && (
                <div className="fixed inset-0 z-[170] flex items-center justify-center bg-slate-950/60 p-4" role="dialog" aria-modal="true" aria-labelledby="preparation-receipt-popup-title">
                    <div className="w-full max-w-sm rounded-3xl bg-white p-5 text-right shadow-2xl">
                        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-50 text-amber-700"><WarningCircle size={28} weight="fill" /></div>
                        <h3 id="preparation-receipt-popup-title" className="mt-4 text-xl font-black text-slate-950">{popup.title}</h3>
                        <p className="mt-2 text-sm font-bold leading-7 text-slate-600">{popup.message}</p>
                        <button type="button" onClick={() => setPopup(null)} className="mt-5 min-h-12 w-full rounded-2xl bg-emerald-800 px-4 text-sm font-black text-white">حسنًا</button>
                    </div>
                </div>
            )}
        </section>
    );
}
