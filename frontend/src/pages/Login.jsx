import { useEffect, useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { Eye, EyeSlash, EnvelopeSimple, LockKey } from "@phosphor-icons/react";
import { useAuth } from "../context/AuthContext";
import { toast } from "sonner";
import api from "../lib/api";
import ForgotPasswordModal from "../components/ForgotPasswordModal";
import { LogoIcon } from "../components/MezanLogo";

const AUTH_BG = "https://static.prod-images.emergentagent.com/jobs/ab0374e5-2a04-4e34-b24c-447b0238a858/images/9126576d79013e8b54614eb6ef7268db1c88914c10825a71376e455fc32c7233.png";

export default function Login() {
    const { login, formatApiErrorDetail } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [showPass, setShowPass] = useState(false);
    const [busy, setBusy] = useState(false);
    const [showForgot, setShowForgot] = useState(false);
    // Defaults to false so the link stays hidden on first paint until config
    // arrives — single-store deployments stay clean by default.
    const [showRegisterLink, setShowRegisterLink] = useState(false);

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

    const submit = async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
            await login(email, password);
            const to = location.state?.from?.pathname || "/";
            navigate(to, { replace: true });
            toast.success("مرحباً بعودتك!");
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تسجيل الدخول");
        } finally {
            setBusy(false);
        }
    };

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

            {showForgot && (
                <ForgotPasswordModal
                    onClose={() => setShowForgot(false)}
                    onSuccess={(em) => setEmail(em)}
                />
            )}
        </div>
    );
}
