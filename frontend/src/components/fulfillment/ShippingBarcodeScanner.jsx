import { useEffect, useRef, useState } from "react";
import {
    Barcode,
    Camera,
    CheckCircle,
    SpinnerGap,
    WarningCircle,
    XCircle,
} from "@phosphor-icons/react";

export default function ShippingBarcodeScanner({
    title,
    description,
    busy = false,
    error = "",
    feedback = null,
    onDetected,
    onFeedbackAction,
    onClose,
}) {
    const videoRef = useRef(null);
    const [manualBarcode, setManualBarcode] = useState("");
    const [cameraError, setCameraError] = useState("");
    const [cameraReady, setCameraReady] = useState(false);

    useEffect(() => {
        setCameraReady(false);
        if (feedback) return undefined;
        let stopped = false;
        let detecting = false;
        let lastValue = "";
        let stream;
        let animationFrame;
        let zxingControls;

        const accept = async (rawValue) => {
            const value = String(rawValue || "").trim();
            if (!value) {
                lastValue = "";
                return;
            }
            if (value === lastValue || detecting || stopped) return;
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
                const formats = ["code_128", "code_39", "qr_code"].filter((format) => supported.includes(format));
                return formats.length
                    ? new globalThis.BarcodeDetector({ formats })
                    : new globalThis.BarcodeDetector();
            } catch {
                return null;
            }
        };

        async function start() {
            if (!navigator.mediaDevices?.getUserMedia) {
                setCameraError("هذا الجهاز لا يتيح الكاميرا للمتصفح؛ استخدم قارئ الباركود أو الإدخال اليدوي.");
                return;
            }
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: { ideal: "environment" },
                        width: { ideal: 1280 },
                        height: { ideal: 720 },
                    },
                    audio: false,
                });
                if (stopped || !videoRef.current) return;
                videoRef.current.srcObject = stream;
                await videoRef.current.play();
                setCameraReady(true);

                const detector = await nativeDetector();
                if (detector) {
                    const detectFrame = async () => {
                        if (stopped || !videoRef.current) return;
                        try {
                            const rows = await detector.detect(videoRef.current);
                            await accept(rows?.[0]?.rawValue);
                        } catch {
                            lastValue = "";
                        }
                        animationFrame = requestAnimationFrame(detectFrame);
                    };
                    animationFrame = requestAnimationFrame(detectFrame);
                    return;
                }

                const { BarcodeFormat, BrowserMultiFormatReader } = await import("@zxing/browser");
                if (stopped || !videoRef.current) return;
                const reader = new BrowserMultiFormatReader(undefined, {
                    delayBetweenScanAttempts: 180,
                    delayBetweenScanSuccess: 600,
                });
                reader.possibleFormats = [
                    BarcodeFormat.CODE_128,
                    BarcodeFormat.CODE_39,
                    BarcodeFormat.QR_CODE,
                ];
                zxingControls = await reader.decodeFromVideoElement(
                    videoRef.current,
                    (result) => {
                        if (result) void accept(result.getText());
                        else lastValue = "";
                    },
                );
            } catch (cameraStartError) {
                const messages = {
                    NotAllowedError: "اسمح لميزان باستخدام الكاميرا من إعدادات المتصفح ثم حاول مرة أخرى.",
                    NotFoundError: "لم يتم العثور على كاميرا في هذا الجهاز.",
                    NotReadableError: "الكاميرا مستخدمة في تطبيق آخر.",
                    SecurityError: "تشغيل الكاميرا يحتاج اتصال HTTPS آمن.",
                };
                setCameraError(messages[cameraStartError?.name] || "تعذّر تشغيل الكاميرا؛ استخدم قارئ الباركود أو الإدخال اليدوي.");
            }
        }

        void start();
        return () => {
            stopped = true;
            if (animationFrame) cancelAnimationFrame(animationFrame);
            zxingControls?.stop?.();
            for (const track of stream?.getTracks?.() || []) track.stop();
        };
    }, [feedback, onDetected]);

    useEffect(() => {
        if (feedback) setManualBarcode("");
    }, [feedback]);

    const submitManual = async (event) => {
        event.preventDefault();
        const value = manualBarcode.trim();
        if (value) await onDetected(value);
    };

    return (
        <div className="fixed inset-0 z-[110] flex items-center justify-center bg-slate-950/90 p-3" dir="rtl" role="dialog" aria-modal="true" aria-label={title} data-testid="shipping-barcode-scanner">
            <div className="w-full max-w-xl overflow-hidden rounded-3xl bg-white shadow-2xl">
                <header className="flex items-start justify-between gap-3 bg-slate-950 p-4 text-white">
                    <div><h3 className="text-lg font-black">{title}</h3><p className="mt-1 text-xs font-bold text-slate-300">{description}</p></div>
                    <button type="button" onClick={onClose} disabled={busy} className="rounded-xl bg-white/10 p-2 disabled:opacity-50" aria-label="إغلاق"><XCircle size={24} /></button>
                </header>
                <div className="space-y-4 p-4">
                    {feedback ? (
                        <div
                            className={`rounded-3xl border p-5 text-center ${feedback.kind === "success" ? "border-emerald-300 bg-emerald-50 text-emerald-950" : "border-amber-300 bg-amber-50 text-amber-950"}`}
                            role="status"
                            aria-live="assertive"
                            data-testid={`shipping-scan-feedback-${feedback.kind}`}
                        >
                            {feedback.kind === "success"
                                ? <CheckCircle size={54} weight="fill" className="mx-auto text-emerald-600" />
                                : <WarningCircle size={54} weight="fill" className="mx-auto text-amber-600" />}
                            <h4 className="mt-3 text-xl font-black">{feedback.title}</h4>
                            <p className="mt-2 text-sm font-bold leading-7">{feedback.message}</p>
                            {feedback.barcode && (
                                <div className="mx-auto mt-3 w-fit rounded-xl bg-white/80 px-4 py-2 font-mono text-sm font-black" dir="ltr">
                                    {feedback.barcode}
                                </div>
                            )}
                            <button
                                type="button"
                                onClick={onFeedbackAction}
                                className={`mt-5 inline-flex min-h-12 w-full items-center justify-center rounded-2xl px-4 font-black text-white ${feedback.kind === "success" ? "bg-emerald-700" : "bg-amber-700"}`}
                                data-testid="shipping-scan-feedback-action"
                            >
                                {feedback.actionLabel || "إغلاق"}
                            </button>
                        </div>
                    ) : (
                        <>
                            <div className="relative aspect-video overflow-hidden rounded-2xl bg-black">
                                <video ref={videoRef} muted playsInline className="h-full w-full object-cover" />
                                {!cameraReady && !cameraError && <div className="absolute inset-0 flex items-center justify-center text-white"><SpinnerGap size={30} className="animate-spin" /></div>}
                                {cameraReady && <div className="pointer-events-none absolute inset-x-10 top-1/2 h-0.5 bg-emerald-400 shadow-[0_0_12px_#34d399]" />}
                            </div>
                            {busy && <div className="rounded-2xl border border-violet-200 bg-violet-50 p-3 text-center text-sm font-black text-violet-950" role="status">تمت قراءة الباركود، جاري التحقق من حالة الشحنة...</div>}
                            {(cameraError || error) && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm font-black text-rose-950" role="alert" data-testid="shipping-scan-error">{error || cameraError}</div>}
                            <form onSubmit={submitManual} className="flex gap-2">
                                <div className="relative min-w-0 flex-1">
                                    <Barcode size={21} className="absolute right-3 top-1/2 -translate-y-1/2 text-violet-700" />
                                    <input value={manualBarcode} onChange={(event) => setManualBarcode(event.target.value)} disabled={busy} autoFocus className="h-12 w-full rounded-xl border border-slate-300 pr-10 pl-3 font-bold" placeholder="أو أدخل رقم الباركود" dir="ltr" />
                                </div>
                                <button type="submit" disabled={busy || !manualBarcode.trim()} className="inline-flex h-12 items-center gap-2 rounded-xl bg-violet-700 px-4 font-black text-white disabled:opacity-50">
                                    {busy ? <SpinnerGap size={20} className="animate-spin" /> : <Camera size={20} />} تحقق
                                </button>
                            </form>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}
