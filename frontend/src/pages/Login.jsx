import { useEffect, useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { Eye, EyeSlash, EnvelopeSimple, LockKey, ShieldCheck, Copy } from "@phosphor-icons/react";
import { useAuth } from "../context/AuthContext";
import { toast } from "sonner";
import api from "../lib/api";
import ForgotPasswordModal from "../components/ForgotPasswordModal";
import { LogoIcon } from "../components/MezanLogo";

const AUTH_BG = "https://static.prod-images.emergentagent.com/jobs/ab0374e5-2a04-4e34-b24c-447b0238a858/images/9126576d79013e8b54614eb6ef7268db1c88914c10825a71376e455fc32c7233.png";

export default function Login() {
    const { login, verifyMfa, refreshUser, formatApiErrorDetail } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [showPass, setShowPass] = useState(false);
    const [busy, setBusy] = useState(false);
    const [showForgot, setShowForgot] = useState(false);
    const [showRegisterLink, setShowRegisterLink] = useState(false);

    // Privileged Owner/Admin MFA flow. `mode=setup` is first enrollment;
    // `mode=verify` is a normal returning login. No auth cookie exists until
    // verifyMfa succeeds.
    const [mfaMode, setMfaMode] = useState(null); // null | setup | verify | recovery
    const [challengeToken, setChallengeToken] = useState("");
    const [setupSecret, setSetupSecret] = useState("");
    const [mfaCode, setMfaCode] = useState("");
    const [recoveryCodes, setRecoveryCodes] = useState([]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const { data } = await api.get("/public/login-config");
                if (!cancelled) setShowRegisterLink(!!data?.show_register_link);
            } catch {
                // Network error → keep default (hidden). Single-store is the safe fallback.
            }
        })();
        return () => { cancelled = true; };
    }, []);

    const finishLogin = () => {
        const to = location.state?.from?.pathname || "/";
        navigate(to, { replace: true });
        toast.success("مرحباً بعودتك!");
    };

    const resetMfa = () => {
        setMfaMode(null);
        setChallengeToken("");
        setSetupSecret("");
        setMfaCode("");
        setRecoveryCodes([]);
    };

    const submit = async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
            const result = await login(email, password);
            if (result?.mfa_setup_required) {
                setChallengeToken(result.challenge_token || "");
                setSetupSecret(result.setup_secret || "");
                setMfaCode("");
                setMfaMode("setup");
                toast.info("فعّل التحقق بخطوتين لإكمال تسجيل الدخول");
                return;
            }
            if (result?.mfa_required) {
                setChallengeToken(result.challenge_token || "");
                setMfaCode("");
                setMfaMode("verify");
                return;
            }
            finishLogin();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تسجيل الدخول");
        } finally {
            setBusy(false);
        }
    };

    const submitMfa = async (e) => {
        e.preventDefault();
        if (!mfaCode.trim()) return;
        setBusy(true);
        try {
            const isEnrollment = mfaMode === "setup";
            const result = await verifyMfa(
                challengeToken,
                mfaCode.trim(),
                { deferRefresh: isEnrollment },
            );
            const codes = Array.isArray(result?.recovery_codes) ? result.recovery_codes : [];
            if (codes.length) {
                setRecoveryCodes(codes);
                setMfaMode("recovery");
                setMfaCode("");
                toast.success("تم تفعيل التحقق بخطوتين");
                return;
            }
            finishLogin();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر التحقق من الرمز");
        } finally {
            setBusy(false);
        }
    };

    const continueAfterRecovery = async () => {
        setBusy(true);
        try {
            const hydrated = await refreshUser();
            if (!hydrated) {
                toast.error("تعذر تأكيد الجلسة. سجّل الدخول من جديد.");
                resetMfa();
                return;
            }
            finishLogin();
        } finally {
            setBusy(false);
        }
    };

    const copyText = async (text, successMessage) => {
        try {
            await navigator.clipboard.writeText(text);
            toast.success(successMessage);
        } catch {
            toast.error("تعذر النسخ تلقائياً. حدّد النص وانسخه يدوياً.");
        }
    };

    const renderPasswordForm = () => (
        <>
            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-foreground mb-3" style={{ fontFamily: "Tajawal" }}>
                أهلاً بعودتك
            </h1>
            <p className="text-muted-foreground mb-10 text-base">
                سجّل دخولك للوصول إلى تقاريرك المالية وتحاليل ملفاتك من منصة سلة.
            </p>

            <form onSubmit={submit} className="space-y-5" data-testid="login-form">
                <div>
                    <label className="block text-sm font-semibold text-foreground mb-2">البريد الإلكتروني</label>
                    <div className="relative">
                        <EnvelopeSimple size={20} className="absolute top-3.5 right-3 text-muted-foreground" />
                        <input
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            placeholder="you@example.com"
                            className="w-full ps-3 pe-10 py-3 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition-shadow"
                            required
                            data-testid="login-email-input"
                            dir="ltr"
                            style={{ textAlign: "right" }}
                        />
                    </div>
                </div>

                <div>
                    <label className="block text-sm font-semibold text-foreground mb-2">كلمة المرور</label>
                    <div className="relative">
                        <LockKey size={20} className="absolute top-3.5 right-3 text-muted-foreground" />
                        <input
                            type={showPass ? "text" : "password"}
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            placeholder="••••••••"
                            className="w-full ps-10 pe-10 py-3 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition-shadow"
                            required
                            minLength={6}
                            data-testid="login-password-input"
                        />
                        <button
                            type="button"
                            onClick={() => setShowPass((s) => !s)}
                            className="absolute top-3.5 left-3 text-muted-foreground hover:text-foreground"
                            data-testid="toggle-password-btn"
                        >
                            {showPass ? <EyeSlash size={20} /> : <Eye size={20} />}
                        </button>
                    </div>
                </div>

                <button
                    type="submit"
                    disabled={busy}
                    className="w-full py-3.5 px-4 bg-brand text-white font-semibold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                    data-testid="login-submit-btn"
                >
                    {busy ? "جاري الدخول…" : "تسجيل الدخول"}
                </button>

                <div className="text-center">
                    <button
                        type="button"
                        onClick={() => setShowForgot(true)}
                        className="text-sm text-brand font-semibold hover:underline"
                        data-testid="forgot-password-link"
                    >
                        نسيت كلمة المرور؟
                    </button>
                </div>
            </form>

            <p className="mt-8 text-center text-sm text-muted-foreground" data-testid="register-link-wrapper">
                {showRegisterLink ? (
                    <>
                        ليس لديك حساب؟{" "}
                        <Link to="/register" className="text-brand font-bold hover:underline" data-testid="link-to-register">
                            إنشاء حساب جديد
                        </Link>
                    </>
                ) : (
                    <span className="text-xs text-muted-foreground/60" data-testid="register-link-hidden">
                        التسجيل مغلق — هذا النظام خاص بمتجر واحد.
                    </span>
                )}
            </p>
        </>
    );

    const renderMfaForm = () => {
        const isSetup = mfaMode === "setup";
        return (
            <div data-testid={isSetup ? "mfa-setup-step" : "mfa-verify-step"}>
                <div className="w-14 h-14 rounded-2xl bg-accent text-brand flex items-center justify-center mb-6">
                    <ShieldCheck size={30} weight="duotone" />
                </div>
                <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight text-foreground mb-3" style={{ fontFamily: "Tajawal" }}>
                    {isSetup ? "تفعيل التحقق بخطوتين" : "رمز التحقق"}
                </h1>
                <p className="text-muted-foreground mb-6 text-sm leading-7">
                    {isSetup
                        ? "حسابات Owner وAdmin في ميزان تتطلب تطبيق مصادقة. افتح Google Authenticator أو Microsoft Authenticator وأضف حساباً جديداً بالمفتاح التالي."
                        : "أدخل الرمز الحالي من تطبيق المصادقة. ويمكنك استخدام أحد رموز الاسترداد المحفوظة إذا لم يكن هاتفك متاحاً."}
                </p>

                {isSetup && (
                    <div className="mb-6 rounded-xl border border-border bg-accent/40 p-4">
                        <div className="text-xs font-semibold text-muted-foreground mb-2">مفتاح الإعداد اليدوي</div>
                        <div className="flex items-center gap-2">
                            <code className="flex-1 text-sm font-bold tracking-wider break-all bg-white rounded-lg border border-border px-3 py-3" dir="ltr">
                                {setupSecret}
                            </code>
                            <button
                                type="button"
                                onClick={() => copyText(setupSecret, "تم نسخ مفتاح الإعداد")}
                                className="w-11 h-11 rounded-lg border border-border bg-white flex items-center justify-center text-brand hover:bg-accent"
                                aria-label="نسخ مفتاح الإعداد"
                                data-testid="copy-mfa-secret"
                            >
                                <Copy size={20} />
                            </button>
                        </div>
                        <div className="text-xs text-muted-foreground mt-3 leading-6">
                            الاسم المقترح داخل التطبيق: <strong>MEZAN</strong> · النوع: <strong>Time based</strong>
                        </div>
                    </div>
                )}

                <form onSubmit={submitMfa} className="space-y-5">
                    <div>
                        <label className="block text-sm font-semibold text-foreground mb-2">
                            {isSetup ? "الرمز المكوّن من 6 أرقام" : "رمز المصادقة أو رمز الاسترداد"}
                        </label>
                        <div className="relative">
                            <ShieldCheck size={20} className="absolute top-3.5 right-3 text-muted-foreground" />
                            <input
                                value={mfaCode}
                                onChange={(e) => setMfaCode(e.target.value)}
                                placeholder={isSetup ? "000000" : "000000 أو XXXX-XXXX-XXXX"}
                                className="w-full ps-3 pe-10 py-3 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition-shadow num"
                                autoComplete="one-time-code"
                                inputMode={isSetup ? "numeric" : "text"}
                                autoFocus
                                required
                                data-testid="mfa-code-input"
                                dir="ltr"
                                style={{ textAlign: "center", letterSpacing: isSetup ? "0.25em" : undefined }}
                            />
                        </div>
                    </div>

                    <button
                        type="submit"
                        disabled={busy || !mfaCode.trim()}
                        className="w-full py-3.5 px-4 bg-brand text-white font-semibold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                        data-testid="mfa-submit-btn"
                    >
                        {busy ? "جاري التحقق…" : isSetup ? "تفعيل والمتابعة" : "تحقق وتسجيل الدخول"}
                    </button>

                    <button
                        type="button"
                        onClick={resetMfa}
                        disabled={busy}
                        className="w-full text-sm text-muted-foreground hover:text-brand font-semibold"
                    >
                        العودة إلى تسجيل الدخول
                    </button>
                </form>
            </div>
        );
    };

    const renderRecoveryCodes = () => (
        <div data-testid="mfa-recovery-codes-step">
            <div className="w-14 h-14 rounded-2xl bg-accent text-brand flex items-center justify-center mb-6">
                <ShieldCheck size={30} weight="duotone" />
            </div>
            <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight text-foreground mb-3" style={{ fontFamily: "Tajawal" }}>
                احفظ رموز الاسترداد
            </h1>
            <p className="text-muted-foreground mb-6 text-sm leading-7">
                تم تفعيل التحقق بخطوتين. هذه الرموز تُعرض مرة واحدة فقط. احتفظ بها في مكان آمن؛ كل رمز يعمل مرة واحدة إذا فقدت الوصول إلى تطبيق المصادقة.
            </p>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-5" dir="ltr">
                {recoveryCodes.map((code) => (
                    <code key={code} className="rounded-lg border border-border bg-white px-3 py-2.5 text-center text-sm font-bold tracking-wider">
                        {code}
                    </code>
                ))}
            </div>

            <button
                type="button"
                onClick={() => copyText(recoveryCodes.join("\n"), "تم نسخ رموز الاسترداد")}
                className="w-full mb-3 py-3 px-4 border border-border bg-white text-brand font-semibold rounded-lg hover:bg-accent transition-colors inline-flex items-center justify-center gap-2"
                data-testid="copy-recovery-codes"
            >
                <Copy size={19} /> نسخ جميع الرموز
            </button>
            <button
                type="button"
                onClick={continueAfterRecovery}
                disabled={busy}
                className="w-full py-3.5 px-4 bg-brand text-white font-semibold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                data-testid="recovery-codes-continue"
            >
                {busy ? "جاري تأكيد الجلسة…" : "حفظت الرموز — المتابعة إلى ميزان"}
            </button>
        </div>
    );

    return (
        <div className="min-h-screen flex" data-testid="login-page">
            {/* Right side — form */}
            <div className="w-full lg:w-1/2 flex items-center justify-center p-8 bg-background">
                <div className="w-full max-w-md animate-fade-in-up">
                    <div className="flex items-center gap-3 mb-10">
                        <LogoIcon size={52} />
                        <div>
                            <div className="text-brand text-2xl font-extrabold tracking-wider" style={{ fontFamily: "Tajawal", letterSpacing: "0.08em" }} data-testid="login-brand-en">
                                <span>MEZ</span><span className="text-accent-green">AN</span>
                            </div>
                            <div className="text-base font-bold text-foreground" style={{ fontFamily: "Tajawal" }} data-testid="login-brand-ar">ميزان</div>
                            <div className="text-xs text-muted-foreground">منصة التحليلات والمحاسبة للتجارة الإلكترونية</div>
                        </div>
                    </div>

                    {mfaMode === null && renderPasswordForm()}
                    {(mfaMode === "setup" || mfaMode === "verify") && renderMfaForm()}
                    {mfaMode === "recovery" && renderRecoveryCodes()}
                </div>
            </div>

            {/* Left side — hero */}
            <div
                className="hidden lg:flex w-1/2 relative items-end p-12"
                style={{
                    backgroundImage: `linear-gradient(135deg, rgba(15,93,70,0.95), rgba(15,93,70,0.75)), url(${AUTH_BG})`,
                    backgroundSize: "cover",
                    backgroundPosition: "center",
                }}
            >
                <div className="text-white max-w-md animate-fade-in-up">
                    <div className="text-gold text-sm font-bold tracking-wider mb-3">MEZAN • منصة سلة • تحليل ذكي</div>
                    <h2 className="text-4xl font-black mb-4 leading-tight" style={{ fontFamily: "Tajawal" }}>
                        افهم أرباحك الحقيقية في دقائق، لا أيام.
                    </h2>
                    <p className="text-white/80 text-base leading-relaxed">
                        ارفع ملف Excel من سلة، أدخل تكاليفك، واحصل على تقرير محاسبي كامل: عمولات الدفع، الشحن، الإعلانات، والربح الصافي — جاهز للتصدير.
                    </p>
                </div>
            </div>

            {showForgot && mfaMode === null && (
                <ForgotPasswordModal
                    onClose={() => setShowForgot(false)}
                    onSuccess={(em) => setEmail(em)}
                />
            )}
        </div>
    );
}
