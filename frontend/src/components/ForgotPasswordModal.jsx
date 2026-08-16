import { useState } from "react";
import { X, LockKey, EnvelopeSimple, ShieldCheck, ArrowRight } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const inputCls =
    "w-full px-3 py-2.5 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition";

export default function ForgotPasswordModal({ onClose, onSuccess }) {
    // step: 'email' → 'reset' → 'done'
    const [step, setStep] = useState("email");
    const [email, setEmail] = useState("");
    const [question, setQuestion] = useState("");
    const [hasQuestion, setHasQuestion] = useState(false);
    const [answer, setAnswer] = useState("");
    const [newPwd, setNewPwd] = useState("");
    const [confirmPwd, setConfirmPwd] = useState("");
    const [busy, setBusy] = useState(false);

    const lookupQuestion = async (e) => {
        e.preventDefault();
        if (!email.trim()) return toast.error("الرجاء إدخال البريد الإلكتروني");
        setBusy(true);
        try {
            const { data } = await api.post("/auth/forgot-password/check", { email: email.trim() });
            setQuestion(data.question);
            setHasQuestion(!!data.has_question);
            setStep("reset");
            if (!data.has_question) {
                toast.warning(data.question || "الاسترداد الذاتي متوقف. تواصل مع مالك النظام.");
            }
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setBusy(false);
        }
    };

    const submitReset = async (e) => {
        e.preventDefault();
        if (!answer.trim()) return toast.error("الرجاء إدخال الإجابة");
        if (newPwd.length < 12) return toast.error("كلمة المرور يجب أن تكون 12 حرفاً على الأقل");
        if (newPwd !== confirmPwd) return toast.error("كلمتا المرور غير متطابقتين");
        setBusy(true);
        try {
            await api.post("/auth/forgot-password/reset", {
                email: email.trim(),
                answer: answer.trim(),
                new_password: newPwd,
            });
            setStep("done");
            toast.success("تم تحديث كلمة المرور — يمكنك الآن تسجيل الدخول.");
            onSuccess?.(email.trim());
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" data-testid="forgot-password-modal">
            <div className="bg-white rounded-xl shadow-xl w-full max-w-md overflow-hidden">
                <header className="flex items-center justify-between border-b border-border px-5 py-3">
                    <div className="flex items-center gap-2">
                        <ShieldCheck size={20} className="text-brand" weight="duotone" />
                        <h2 className="font-bold text-lg">استرجاع كلمة المرور</h2>
                    </div>
                    <button onClick={onClose} className="p-1.5 rounded hover:bg-accent" data-testid="forgot-close-btn">
                        <X size={20} />
                    </button>
                </header>

                {step === "email" && (
                    <form onSubmit={lookupQuestion} className="p-5 space-y-4">
                        <p className="text-sm text-muted-foreground">أدخل بريدك الإلكتروني للتحقق من خيارات الاسترداد المتاحة.</p>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">البريد الإلكتروني</label>
                            <div className="relative">
                                <EnvelopeSimple size={18} className="absolute top-3 right-3 text-muted-foreground" />
                                <input
                                    type="email"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    className={`${inputCls} ps-3 pe-9`}
                                    placeholder="you@example.com"
                                    dir="ltr"
                                    style={{ textAlign: "right" }}
                                    data-testid="forgot-email-input"
                                    autoFocus
                                />
                            </div>
                        </div>
                        <button
                            type="submit"
                            disabled={busy}
                            className="w-full py-2.5 bg-brand text-white font-semibold rounded-lg bg-brand-hover disabled:opacity-60 inline-flex items-center justify-center gap-2"
                            data-testid="forgot-next-btn"
                        >
                            {busy ? "جاري التحقق…" : "متابعة"}
                            {!busy && <ArrowRight size={18} className="rotate-180" />}
                        </button>
                    </form>
                )}

                {step === "reset" && (
                    <form onSubmit={submitReset} className="p-5 space-y-4">
                        <div className="p-3 rounded-lg bg-slate-50 border border-border">
                            <div className="text-xs text-muted-foreground mb-0.5">سؤال الأمان</div>
                            <div className="font-bold text-foreground" data-testid="forgot-question-text">{question}</div>
                        </div>
                        {!hasQuestion && (
                            <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-900">
                                الاسترداد الذاتي متوقف لحماية الحسابات. تواصل مع مالك النظام لإعادة تعيين كلمة المرور.
                            </div>
                        )}
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">الإجابة</label>
                            <input
                                value={answer}
                                onChange={(e) => setAnswer(e.target.value)}
                                className={inputCls}
                                placeholder="إجابتك على السؤال أعلاه"
                                data-testid="forgot-answer-input"
                                disabled={!hasQuestion}
                                autoFocus
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">كلمة المرور الجديدة</label>
                            <div className="relative">
                                <LockKey size={18} className="absolute top-3 right-3 text-muted-foreground" />
                                <input
                                    type="password"
                                    value={newPwd}
                                    onChange={(e) => setNewPwd(e.target.value)}
                                    className={`${inputCls} ps-3 pe-9`}
                                    placeholder="12 حرفاً على الأقل"
                                    data-testid="forgot-new-pwd-input"
                                    disabled={!hasQuestion}
                                />
                            </div>
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-1.5">تأكيد كلمة المرور</label>
                            <input
                                type="password"
                                value={confirmPwd}
                                onChange={(e) => setConfirmPwd(e.target.value)}
                                className={inputCls}
                                placeholder="أعد كتابتها"
                                data-testid="forgot-confirm-pwd-input"
                                disabled={!hasQuestion}
                            />
                        </div>
                        <div className="flex gap-2">
                            <button
                                type="button"
                                onClick={() => setStep("email")}
                                className="flex-1 py-2.5 border border-border text-sm font-semibold rounded-lg hover:bg-accent"
                                data-testid="forgot-back-btn"
                            >
                                رجوع
                            </button>
                            <button
                                type="submit"
                                disabled={busy || !hasQuestion}
                                className="flex-1 py-2.5 bg-brand text-white text-sm font-semibold rounded-lg bg-brand-hover disabled:opacity-60"
                                data-testid="forgot-submit-btn"
                            >
                                {busy ? "جاري الحفظ…" : "إعادة تعيين كلمة المرور"}
                            </button>
                        </div>
                    </form>
                )}

                {step === "done" && (
                    <div className="p-6 text-center space-y-4" data-testid="forgot-done">
                        <div className="w-14 h-14 rounded-full bg-emerald-100 text-emerald-600 mx-auto flex items-center justify-center">
                            <ShieldCheck size={32} weight="fill" />
                        </div>
                        <h3 className="font-bold text-lg">تم بنجاح!</h3>
                        <p className="text-sm text-muted-foreground">تم تحديث كلمة المرور. سجّل الدخول بكلمة المرور الجديدة.</p>
                        <button
                            onClick={onClose}
                            className="w-full py-2.5 bg-brand text-white font-semibold rounded-lg bg-brand-hover"
                            data-testid="forgot-done-close-btn"
                        >
                            العودة لتسجيل الدخول
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
