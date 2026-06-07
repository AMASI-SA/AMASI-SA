import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { User, EnvelopeSimple, LockKey } from "@phosphor-icons/react";
import { useAuth } from "../context/AuthContext";
import { toast } from "sonner";
import { LogoIcon } from "../components/MezanLogo";

const AUTH_BG = "https://static.prod-images.emergentagent.com/jobs/ab0374e5-2a04-4e34-b24c-447b0238a858/images/9126576d79013e8b54614eb6ef7268db1c88914c10825a71376e455fc32c7233.png";

export default function Register() {
    const { register, formatApiErrorDetail } = useAuth();
    const navigate = useNavigate();
    const [name, setName] = useState("");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [busy, setBusy] = useState(false);

    const submit = async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
            await register(name, email, password);
            toast.success("تم إنشاء حسابك بنجاح!");
            navigate("/", { replace: true });
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر إنشاء الحساب");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="min-h-screen flex" data-testid="register-page">
            <div className="w-full lg:w-1/2 flex items-center justify-center p-8 bg-background">
                <div className="w-full max-w-md animate-fade-in-up">
                    <div className="flex items-center gap-3 mb-10">
                        <LogoIcon size={52} />
                        <div>
                            <div className="text-brand text-2xl font-extrabold tracking-wider" style={{ fontFamily: "Tajawal", letterSpacing: "0.08em" }}>
                                <span>MEZ</span><span className="text-accent-green">AN</span>
                            </div>
                            <div className="text-base font-bold text-foreground" style={{ fontFamily: "Tajawal" }}>ميزان</div>
                            <div className="text-xs text-muted-foreground">منصة التحليلات والمحاسبة للتجارة الإلكترونية</div>
                        </div>
                    </div>

                    <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-foreground mb-3" style={{ fontFamily: "Tajawal" }}>
                        ابدأ مجاناً
                    </h1>
                    <p className="text-muted-foreground mb-10 text-base">
                        أنشئ حساباً وابدأ بتحليل ملفات سلة وحساب أرباحك الصافية فوراً.
                    </p>

                    <form onSubmit={submit} className="space-y-5" data-testid="register-form">
                        <div>
                            <label className="block text-sm font-semibold mb-2">الاسم</label>
                            <div className="relative">
                                <User size={20} className="absolute top-3.5 right-3 text-muted-foreground" />
                                <input
                                    type="text"
                                    value={name}
                                    onChange={(e) => setName(e.target.value)}
                                    placeholder="اسمك الكامل"
                                    className="w-full ps-3 pe-10 py-3 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                    required
                                    minLength={1}
                                    data-testid="register-name-input"
                                />
                            </div>
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-2">البريد الإلكتروني</label>
                            <div className="relative">
                                <EnvelopeSimple size={20} className="absolute top-3.5 right-3 text-muted-foreground" />
                                <input
                                    type="email"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    placeholder="you@example.com"
                                    className="w-full ps-3 pe-10 py-3 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                    required
                                    data-testid="register-email-input"
                                    dir="ltr"
                                    style={{ textAlign: "right" }}
                                />
                            </div>
                        </div>
                        <div>
                            <label className="block text-sm font-semibold mb-2">كلمة المرور (٦ أحرف فأكثر)</label>
                            <div className="relative">
                                <LockKey size={20} className="absolute top-3.5 right-3 text-muted-foreground" />
                                <input
                                    type="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    placeholder="••••••••"
                                    className="w-full ps-3 pe-10 py-3 text-base border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                    required
                                    minLength={6}
                                    data-testid="register-password-input"
                                />
                            </div>
                        </div>
                        <button
                            type="submit"
                            disabled={busy}
                            className="w-full py-3.5 px-4 bg-brand text-white font-semibold rounded-lg bg-brand-hover transition-colors disabled:opacity-60"
                            data-testid="register-submit-btn"
                        >
                            {busy ? "جاري إنشاء الحساب…" : "إنشاء حساب"}
                        </button>
                    </form>

                    <p className="mt-8 text-center text-sm text-muted-foreground">
                        لديك حساب بالفعل؟{" "}
                        <Link to="/login" className="text-brand font-bold hover:underline" data-testid="link-to-login">
                            تسجيل الدخول
                        </Link>
                    </p>
                </div>
            </div>

            <div
                className="hidden lg:flex w-1/2 relative items-end p-12"
                style={{
                    backgroundImage: `linear-gradient(135deg, rgba(10,54,34,0.92), rgba(10,54,34,0.7)), url(${AUTH_BG})`,
                    backgroundSize: "cover",
                    backgroundPosition: "center",
                }}
            >
                <div className="text-white max-w-md animate-fade-in-up">
                    <div className="text-gold text-sm font-bold tracking-wider mb-3">رؤية مالية أوضح</div>
                    <h2 className="text-4xl font-black mb-4 leading-tight" style={{ fontFamily: "Tajawal" }}>
                        كل أرقامك في مكان واحد.
                    </h2>
                    <p className="text-white/80 text-base leading-relaxed">
                        مبيعات • عمولات الدفع • تكاليف الشحن • مصاريف الإعلانات — تحليل تلقائي بدقة، وتقارير قابلة للتصدير بصيغة PDF و Excel.
                    </p>
                </div>
            </div>
        </div>
    );
}
