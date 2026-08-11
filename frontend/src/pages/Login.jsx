import { useEffect, useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { Eye, EyeSlash, EnvelopeSimple, LockKey, ShieldCheck, Copy } from "@phosphor-icons/react";
import { useAuth } from "../context/AuthContext";
import { toast } from "sonner";
import api from "../lib/api";
import ForgotPasswordModal from "../components/ForgotPasswordModal";
import { LogoIcon } from "../components/MezanLogo";

const AUTH_BG = "https://static.prod-images.emergentagent.com/jobs/ab0374e5-2a04-4e34-b24c-447b0238a858/images/9126576d79013e8b54614eb6ef7268db1c88914c10825a71376e455fc32c7233.png";

function supportsPlatformPasskey() {
    return typeof window !== "undefined"
        && typeof window.PublicKeyCredential !== "undefined"
        && !!navigator?.credentials;
}

function base64urlToArrayBuffer(value) {
    const normalized = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
    const binary = window.atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
}

function arrayBufferToBase64url(value) {
    if (value == null) return null;
    const bytes = new Uint8Array(value);
    let binary = "";
    for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
    return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function creationOptionsFromJson(options = {}) {
    return {
        ...options,
        challenge: base64urlToArrayBuffer(options.challenge),
        user: {
            ...options.user,
            id: base64urlToArrayBuffer(options?.user?.id),
        },
        excludeCredentials: (options.excludeCredentials || []).map((item) => ({
            ...item,
            id: base64urlToArrayBuffer(item.id),
        })),
    };
}

function requestOptionsFromJson(options = {}) {
    return {
        ...options,
        challenge: base64urlToArrayBuffer(options.challenge),
        allowCredentials: (options.allowCredentials || []).map((item) => ({
            ...item,
            id: base64urlToArrayBuffer(item.id),
        })),
    };
}

function credentialToJson(credential) {
    const response = credential?.response || {};
    const serializedResponse = {
        clientDataJSON: arrayBufferToBase64url(response.clientDataJSON),
    };
    if (response.attestationObject) {
        serializedResponse.attestationObject = arrayBufferToBase64url(response.attestationObject);
    }
    if (response.authenticatorData) {
        serializedResponse.authenticatorData = arrayBufferToBase64url(response.authenticatorData);
    }
    if (response.signature) {
        serializedResponse.signature = arrayBufferToBase64url(response.signature);
    }
    if (response.userHandle) {
        serializedResponse.userHandle = arrayBufferToBase64url(response.userHandle);
    }
    if (typeof response.getTransports === "function") {
        serializedResponse.transports = response.getTransports();
    }
    return {
        id: credential.id,
        rawId: arrayBufferToBase64url(credential.rawId),
        type: credential.type,
        authenticatorAttachment: credential.authenticatorAttachment || undefined,
        response: serializedResponse,
        clientExtensionResults: credential.getClientExtensionResults?.() || {},
    };
}

function deviceLabel() {
    return navigator?.userAgentData?.platform || navigator?.platform || "هذا الجهاز";
}

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

    // Privileged auth flow:
    // Owner: bootstrap → setup → recovery on first TOTP enrollment, then TOTP
    // or a trusted-device passkey. Admin/sensitive employees can instead
    // receive a six-digit email OTP when that deployment feature is enabled.
    const [mfaMode, setMfaMode] = useState(null); // null | bootstrap | setup | verify | recovery | passkey | trust
    const [bootstrapCode, setBootstrapCode] = useState("");
    const [challengeToken, setChallengeToken] = useState("");
    const [setupSecret, setSetupSecret] = useState("");
    const [mfaCode, setMfaCode] = useState("");
    const [mfaChannel, setMfaChannel] = useState("totp");
    const [maskedEmail, setMaskedEmail] = useState("");
    const [resendSeconds, setResendSeconds] = useState(0);
    const [recoveryCodes, setRecoveryCodes] = useState([]);
    const [verifiedRole, setVerifiedRole] = useState("");
    const [passkeyChallengeId, setPasskeyChallengeId] = useState("");
    const [passkeyOptions, setPasskeyOptions] = useState(null);

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

    useEffect(() => {
        if (mfaMode !== "verify" || mfaChannel !== "email" || resendSeconds <= 0) return undefined;
        const timer = window.setTimeout(() => {
            setResendSeconds((seconds) => Math.max(0, seconds - 1));
        }, 1000);
        return () => window.clearTimeout(timer);
    }, [mfaMode, mfaChannel, resendSeconds]);

    const finishLogin = () => {
        const to = location.state?.from?.pathname || "/";
        navigate(to, { replace: true });
        toast.success("مرحباً بعودتك!");
    };

    const hydrateAndFinish = async () => {
        const hydrated = await refreshUser();
        if (!hydrated) {
            toast.error("تعذر تأكيد الجلسة. سجّل الدخول من جديد.");
            resetMfa();
            return false;
        }
        finishLogin();
        return true;
    };

    const resetMfa = () => {
        setMfaMode(null);
        setBootstrapCode("");
        setChallengeToken("");
        setSetupSecret("");
        setMfaCode("");
        setMfaChannel("totp");
        setMaskedEmail("");
        setResendSeconds(0);
        setRecoveryCodes([]);
        setVerifiedRole("");
        setPasskeyChallengeId("");
        setPasskeyOptions(null);
    };

    const applyLoginResult = (result) => {
        if (result?.passkey_required) {
            setPasskeyChallengeId(result.challenge_id || "");
            setPasskeyOptions(result.webauthn_options || null);
            setMfaMode("passkey");
            return true;
        }
        if (result?.mfa_bootstrap_required) {
            setMfaChannel("totp");
            setBootstrapCode("");
            setMfaMode("bootstrap");
            toast.info("يلزم رمز التفعيل الأولي لربط تطبيق المصادقة لأول مرة");
            return true;
        }
        if (result?.mfa_setup_required) {
            setMfaChannel("totp");
            setChallengeToken(result.challenge_token || "");
            setSetupSecret(result.setup_secret || "");
            setMfaCode("");
            setMfaMode("setup");
            toast.info("فعّل التحقق بخطوتين لإكمال تسجيل الدخول");
            return true;
        }
        if (result?.mfa_required) {
            const channel = result?.mfa_channel === "email" ? "email" : "totp";
            setMfaChannel(channel);
            setChallengeToken(result.challenge_token || "");
            setMaskedEmail(result.masked_email || "");
            setResendSeconds(Math.max(0, Number(result.resend_after_seconds || 0)));
            setMfaCode("");
            setMfaMode("verify");
            if (channel === "email") {
                toast.info("تم إرسال رمز التحقق إلى بريد حسابك");
            }
            return true;
        }
        return false;
    };

    const submit = async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
            const result = await login(email, password);
            if (!applyLoginResult(result)) finishLogin();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تسجيل الدخول");
        } finally {
            setBusy(false);
        }
    };

    const submitBootstrap = async (e) => {
        e.preventDefault();
        if (!bootstrapCode.trim()) return;
        setBusy(true);
        try {
            const result = await login(email, password, bootstrapCode.trim());
            if (!applyLoginResult(result)) finishLogin();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر التحقق من رمز التفعيل الأولي");
        } finally {
            setBusy(false);
        }
    };

    const submitMfa = async (e) => {
        e.preventDefault();
        if (!mfaCode.trim()) return;
        setBusy(true);
        try {
            if (mfaMode === "verify" && mfaChannel === "email") {
                await api.post("/auth/email-otp/verify", {
                    challenge_token: challengeToken,
                    code: mfaCode.trim(),
                });
                await hydrateAndFinish();
                return;
            }

            const result = await verifyMfa(
                challengeToken,
                mfaCode.trim(),
                { deferRefresh: true },
            );
            const role = String(result?.role || "").toLowerCase();
            setVerifiedRole(role);
            const codes = Array.isArray(result?.recovery_codes) ? result.recovery_codes : [];
            if (codes.length) {
                setRecoveryCodes(codes);
                setMfaMode("recovery");
                setMfaCode("");
                toast.success("تم تفعيل التحقق بخطوتين");
                return;
            }
            if (role === "owner") {
                setMfaMode("trust");
                setMfaCode("");
                return;
            }
            await hydrateAndFinish();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر التحقق من الرمز");
        } finally {
            setBusy(false);
        }
    };

    const resendEmailOtp = async () => {
        if (busy || mfaChannel !== "email" || resendSeconds > 0 || !challengeToken) return;
        setBusy(true);
        try {
            const { data } = await api.post("/auth/email-otp/resend", {
                challenge_token: challengeToken,
            });
            setChallengeToken(data?.challenge_token || challengeToken);
            setMaskedEmail(data?.masked_email || maskedEmail);
            setResendSeconds(Math.max(0, Number(data?.resend_after_seconds || 60)));
            setMfaCode("");
            toast.success("تم إرسال رمز تحقق جديد إلى بريدك");
        } catch (err) {
            const retryAfter = Number(
                err.response?.data?.retry_after_seconds
                || err.response?.headers?.["retry-after"]
                || 0,
            );
            if (retryAfter > 0) setResendSeconds(Math.ceil(retryAfter));
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر إعادة إرسال الرمز");
        } finally {
            setBusy(false);
        }
    };

    const continueAfterRecovery = async () => {
        if (verifiedRole === "owner") {
            setMfaMode("trust");
            return;
        }
        setBusy(true);
        try {
            await hydrateAndFinish();
        } finally {
            setBusy(false);
        }
    };

    const submitPasskey = async () => {
        if (!passkeyChallengeId || !passkeyOptions) return;
        if (!supportsPlatformPasskey()) {
            toast.error("هذا المتصفح لا يدعم بصمة/PIN الجهاز. استخدم Google Authenticator.");
            return;
        }
        setBusy(true);
        try {
            const credential = await navigator.credentials.get({
                publicKey: requestOptionsFromJson(passkeyOptions),
            });
            if (!credential) throw new Error("passkey_cancelled");
            await api.post("/auth/passkey/authenticate/verify", {
                challenge_id: passkeyChallengeId,
                credential: credentialToJson(credential),
            });
            await hydrateAndFinish();
        } catch (err) {
            if (err?.name === "NotAllowedError") {
                toast.info("تم إلغاء التحقق من الجهاز. يمكنك استخدام Google Authenticator.");
            } else {
                toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر التحقق من الجهاز");
            }
        } finally {
            setBusy(false);
        }
    };

    const useTotpFallback = async () => {
        setBusy(true);
        try {
            const result = await login(email, password, "", true);
            if (!applyLoginResult(result)) finishLogin();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر بدء التحقق من التطبيق");
        } finally {
            setBusy(false);
        }
    };

    const trustThisDevice = async () => {
        if (!supportsPlatformPasskey()) {
            toast.error("هذا المتصفح لا يدعم Windows Hello / بصمة أو PIN الجهاز. يمكنك المتابعة بدون حفظ الجهاز.");
            return;
        }
        setBusy(true);
        try {
            const { data: start } = await api.post("/auth/passkey/trust/options", {});
            const ceremony = start?.ceremony;
            let credential;
            if (ceremony === "renew") {
                credential = await navigator.credentials.get({
                    publicKey: requestOptionsFromJson(start.webauthn_options || {}),
                });
            } else {
                credential = await navigator.credentials.create({
                    publicKey: creationOptionsFromJson(start.webauthn_options || {}),
                });
            }
            if (!credential) throw new Error("passkey_cancelled");
            await api.post("/auth/passkey/trust/verify", {
                challenge_id: start.challenge_id,
                credential: credentialToJson(credential),
                device_label: deviceLabel(),
            });
            toast.success(`تم توثيق هذا الجهاز لمدة ${start?.trust_days || 30} يومًا`);
            await hydrateAndFinish();
        } catch (err) {
            if (err?.name === "NotAllowedError") {
                toast.info("تم إلغاء حفظ الجهاز. يمكنك المتابعة بدون حفظه.");
            } else {
                toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر حفظ الجهاز الموثوق");
            }
        } finally {
            setBusy(false);
        }
    };

    const skipTrustedDevice = async () => {
        setBusy(true);
        try {
            await hydrateAndFinish();
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

    const renderBootstrapForm = () => (
        <div data-testid="mfa-bootstrap-step">
            <div className="w-14 h-14 rounded-2xl bg-accent text-brand flex items-center justify-center mb-6">
                <LockKey size={30} weight="duotone" />
            </div>
            <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight text-foreground mb-3" style={{ fontFamily: "Tajawal" }}>
                رمز التفعيل الأولي
            </h1>
            <p className="text-muted-foreground mb-6 text-sm leading-7">
                هذه خطوة تُستخدم مرة واحدة فقط قبل ربط تطبيق المصادقة لأول مرة. أدخل رمز التفعيل الأولي الذي تم ضبطه في إعدادات نشر ميزان. لا يتم حفظ هذا الرمز في قاعدة بيانات ميزان.
            </p>
            <form onSubmit={submitBootstrap} className="space-y-5">
                <div>
                    <label className="block text-sm font-semibold text-foreground mb-2">رمز التفعيل الأولي</label>
                    <div className="relative">
                        <ShieldCheck size={20} className="absolute top-3.5 right-3 text-muted-foreground" />
                        <input
                            type="password"
                            value={bootstrapCode}
                            onChange={(e) => setBootstrapCode(e.target.value)}
                            placeholder="أدخل رمز التفعيل"
                            className="w-full ps-3 pe-10 py-3 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition-shadow"
                            autoComplete="off"
                            autoFocus
                            required
                            data-testid="mfa-bootstrap-input"
                            dir="ltr"
                        />
                    </div>
                    <p className="text-xs text-muted-foreground mt-2">
                        خمس محاولات غير صحيحة تخضع لنفس حماية حظر الجهاز المفعّلة في صفحة الدخول.
                    </p>
                </div>
                <button
                    type="submit"
                    disabled={busy || !bootstrapCode.trim()}
                    className="w-full py-3.5 px-4 bg-brand text-white font-semibold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                    data-testid="mfa-bootstrap-submit"
                >
                    {busy ? "جاري التحقق…" : "تحقق وابدأ الربط"}
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

    const renderMfaForm = () => {
        const isSetup = mfaMode === "setup";
        const isEmailOtp = !isSetup && mfaChannel === "email";
        return (
            <div data-testid={isSetup ? "mfa-setup-step" : isEmailOtp ? "email-otp-verify-step" : "mfa-verify-step"}>
                <div className="w-14 h-14 rounded-2xl bg-accent text-brand flex items-center justify-center mb-6">
                    {isEmailOtp ? <EnvelopeSimple size={30} weight="duotone" /> : <ShieldCheck size={30} weight="duotone" />}
                </div>
                <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight text-foreground mb-3" style={{ fontFamily: "Tajawal" }}>
                    {isSetup ? "تفعيل التحقق بخطوتين" : isEmailOtp ? "رمز البريد الإلكتروني" : "رمز التحقق"}
                </h1>
                <p className="text-muted-foreground mb-6 text-sm leading-7">
                    {isSetup
                        ? "حساب المالك في ميزان يتطلب تطبيق مصادقة. افتح Google Authenticator أو Microsoft Authenticator وأضف حساباً جديداً بالمفتاح التالي."
                        : isEmailOtp
                            ? <>أرسلنا رمزًا من 6 أرقام إلى بريد الحساب <strong dir="ltr">{maskedEmail || "المسجل"}</strong>. الرمز صالح لمدة قصيرة ويعمل مرة واحدة فقط.</>
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
                            {isSetup ? "الرمز المكوّن من 6 أرقام" : isEmailOtp ? "رمز البريد الإلكتروني" : "رمز المصادقة أو رمز الاسترداد"}
                        </label>
                        <div className="relative">
                            {isEmailOtp ? <EnvelopeSimple size={20} className="absolute top-3.5 right-3 text-muted-foreground" /> : <ShieldCheck size={20} className="absolute top-3.5 right-3 text-muted-foreground" />}
                            <input
                                value={mfaCode}
                                onChange={(e) => {
                                    const value = isEmailOtp ? e.target.value.replace(/\D/g, "").slice(0, 6) : e.target.value;
                                    setMfaCode(value);
                                }}
                                placeholder={isSetup || isEmailOtp ? "000000" : "000000 أو XXXX-XXXX-XXXX"}
                                className="w-full ps-3 pe-10 py-3 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition-shadow num"
                                autoComplete="one-time-code"
                                inputMode={isSetup || isEmailOtp ? "numeric" : "text"}
                                autoFocus
                                required
                                data-testid={isEmailOtp ? "email-otp-code-input" : "mfa-code-input"}
                                dir="ltr"
                                style={{ textAlign: "center", letterSpacing: isSetup || isEmailOtp ? "0.25em" : undefined }}
                            />
                        </div>
                    </div>

                    <button
                        type="submit"
                        disabled={busy || !mfaCode.trim() || (isEmailOtp && mfaCode.length !== 6)}
                        className="w-full py-3.5 px-4 bg-brand text-white font-semibold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                        data-testid={isEmailOtp ? "email-otp-submit-btn" : "mfa-submit-btn"}
                    >
                        {busy ? "جاري التحقق…" : isSetup ? "تفعيل والمتابعة" : "تحقق وتسجيل الدخول"}
                    </button>

                    {isEmailOtp && (
                        <button
                            type="button"
                            onClick={resendEmailOtp}
                            disabled={busy || resendSeconds > 0}
                            className="w-full py-3 px-4 border border-border bg-white text-brand font-semibold rounded-lg hover:bg-accent disabled:opacity-50"
                            data-testid="email-otp-resend-btn"
                        >
                            {resendSeconds > 0 ? `إعادة الإرسال خلال ${resendSeconds}ث` : "إعادة إرسال رمز جديد"}
                        </button>
                    )}

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
                {busy ? "جاري تأكيد الجلسة…" : "حفظت الرموز — المتابعة"}
            </button>
        </div>
    );

    const renderPasskeyForm = () => (
        <div data-testid="passkey-login-step">
            <div className="w-14 h-14 rounded-2xl bg-accent text-brand flex items-center justify-center mb-6">
                <ShieldCheck size={30} weight="duotone" />
            </div>
            <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight text-foreground mb-3" style={{ fontFamily: "Tajawal" }}>
                تحقق من هذا الجهاز
            </h1>
            <p className="text-muted-foreground mb-6 text-sm leading-7">
                هذا جهاز موثوق. استخدم Windows Hello أو البصمة/الوجه أو PIN الجهاز بدل فتح Google Authenticator.
            </p>
            <button
                type="button"
                onClick={submitPasskey}
                disabled={busy || !supportsPlatformPasskey()}
                className="w-full py-3.5 px-4 bg-brand text-white font-semibold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                data-testid="passkey-login-submit"
            >
                {busy ? "جاري التحقق…" : "التحقق ببصمة / PIN الجهاز"}
            </button>
            {!supportsPlatformPasskey() && (
                <p className="text-xs text-muted-foreground mt-3 text-center">
                    هذا المتصفح لا يدعم تحقق الجهاز المحلي؛ استخدم تطبيق المصادقة.
                </p>
            )}
            <button
                type="button"
                onClick={useTotpFallback}
                disabled={busy}
                className="w-full mt-4 py-3 px-4 border border-border bg-white text-brand font-semibold rounded-lg hover:bg-accent transition-colors"
                data-testid="passkey-use-totp"
            >
                استخدام Google Authenticator بدلاً من ذلك
            </button>
            <button
                type="button"
                onClick={resetMfa}
                disabled={busy}
                className="w-full mt-4 text-sm text-muted-foreground hover:text-brand font-semibold"
            >
                العودة إلى تسجيل الدخول
            </button>
        </div>
    );

    const renderTrustDevice = () => (
        <div data-testid="trusted-device-step">
            <div className="w-14 h-14 rounded-2xl bg-accent text-brand flex items-center justify-center mb-6">
                <ShieldCheck size={30} weight="duotone" />
            </div>
            <h1 className="text-3xl sm:text-4xl font-extrabold tracking-tight text-foreground mb-3" style={{ fontFamily: "Tajawal" }}>
                الوثوق بهذا الجهاز؟
            </h1>
            <p className="text-muted-foreground mb-5 text-sm leading-7">
                يمكنك حفظ هذا الجهاز لمدة <strong>30 يومًا</strong>. في المرات القادمة ستدخل بكلمة المرور ثم Windows Hello أو البصمة/PIN الجهاز، بدون الحاجة لفتح Google Authenticator كل مرة.
            </p>
            <div className="rounded-xl border border-border bg-accent/40 p-4 mb-6 text-xs text-muted-foreground leading-6">
                ميزان لا يستلم بصمتك ولا PIN جهازك؛ التحقق يتم داخل الجهاز. عند انتهاء 30 يومًا سيطلب منك رمز المصادقة مرة واحدة لتجديد الثقة.
            </div>
            <button
                type="button"
                onClick={trustThisDevice}
                disabled={busy || !supportsPlatformPasskey()}
                className="w-full py-3.5 px-4 bg-brand text-white font-semibold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                data-testid="trust-device-submit"
            >
                {busy ? "جاري حفظ الجهاز…" : "نعم، وثّق هذا الجهاز"}
            </button>
            {!supportsPlatformPasskey() && (
                <p className="text-xs text-muted-foreground mt-3 text-center">
                    التحقق المحلي غير متاح في هذا المتصفح؛ يمكنك الدخول بدون حفظ الجهاز.
                </p>
            )}
            <button
                type="button"
                onClick={skipTrustedDevice}
                disabled={busy}
                className="w-full mt-4 py-3 px-4 border border-border bg-white text-brand font-semibold rounded-lg hover:bg-accent transition-colors"
                data-testid="trust-device-skip"
            >
                ليس الآن — المتابعة إلى ميزان
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
                    {mfaMode === "bootstrap" && renderBootstrapForm()}
                    {(mfaMode === "setup" || mfaMode === "verify") && renderMfaForm()}
                    {mfaMode === "recovery" && renderRecoveryCodes()}
                    {mfaMode === "passkey" && renderPasskeyForm()}
                    {mfaMode === "trust" && renderTrustDevice()}
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
