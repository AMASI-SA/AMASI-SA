import { useState } from "react";
import { UserCircle, EnvelopeSimple, LockKey, ShieldCheck, FloppyDisk, Eye, EyeSlash } from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { useAuth } from "../context/AuthContext";

function Section({ title, subtitle, icon: Icon, children, testid }) {
    return (
        <div className="bg-white rounded-xl border border-border p-6 shadow-sm" data-testid={testid}>
            <div className="flex items-start gap-3 mb-5">
                <div className="w-10 h-10 rounded-lg bg-brand/10 text-brand flex items-center justify-center shrink-0">
                    <Icon size={22} weight="duotone" />
                </div>
                <div>
                    <h2 className="text-lg font-bold text-foreground">{title}</h2>
                    {subtitle && <p className="text-sm text-muted-foreground mt-0.5">{subtitle}</p>}
                </div>
            </div>
            {children}
        </div>
    );
}

function Field({ label, children, testid }) {
    return (
        <div data-testid={testid}>
            <label className="block text-sm font-semibold text-foreground mb-1.5">{label}</label>
            {children}
        </div>
    );
}

const inputCls =
    "w-full px-3 py-2.5 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand focus:border-brand transition";

function PasswordInput({ value, onChange, testid, placeholder }) {
    const [show, setShow] = useState(false);
    return (
        <div className="relative">
            <input
                type={show ? "text" : "password"}
                value={value}
                onChange={onChange}
                placeholder={placeholder}
                className={`${inputCls} pe-10`}
                data-testid={testid}
            />
            <button
                type="button"
                onClick={() => setShow((s) => !s)}
                className="absolute top-2.5 left-3 text-muted-foreground hover:text-foreground"
                data-testid={`${testid}-toggle`}
                tabIndex={-1}
            >
                {show ? <EyeSlash size={18} /> : <Eye size={18} />}
            </button>
        </div>
    );
}

function SaveButton({ busy, testid, children = "حفظ" }) {
    return (
        <button
            type="submit"
            disabled={busy}
            className="inline-flex items-center gap-2 px-5 py-2.5 bg-brand text-white text-sm font-semibold rounded-lg bg-brand-hover transition disabled:opacity-60"
            data-testid={testid}
        >
            <FloppyDisk size={18} weight="duotone" />
            {busy ? "جاري الحفظ…" : children}
        </button>
    );
}

export default function Profile() {
    const { user, refreshUser } = useAuth();

    // Name form
    const [name, setName] = useState(user?.name || "");
    const [savingName, setSavingName] = useState(false);

    // Email form
    const [newEmail, setNewEmail] = useState("");
    const [emailPwd, setEmailPwd] = useState("");
    const [savingEmail, setSavingEmail] = useState(false);

    // Password form
    const [curPwd, setCurPwd] = useState("");
    const [newPwd, setNewPwd] = useState("");
    const [confirmPwd, setConfirmPwd] = useState("");
    const [savingPwd, setSavingPwd] = useState(false);

    // Security question form
    const [question, setQuestion] = useState("");
    const [answer, setAnswer] = useState("");
    const [secPwd, setSecPwd] = useState("");
    const [savingSec, setSavingSec] = useState(false);

    const submitName = async (e) => {
        e.preventDefault();
        if (!name.trim()) {
            toast.error("الاسم لا يمكن أن يكون فارغاً");
            return;
        }
        setSavingName(true);
        try {
            await api.put("/auth/profile/name", { name: name.trim() });
            toast.success("تم تحديث الاسم بنجاح");
            await refreshUser?.();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setSavingName(false);
        }
    };

    const submitEmail = async (e) => {
        e.preventDefault();
        if (!newEmail.trim() || !emailPwd) {
            toast.error("الرجاء إدخال البريد الجديد وكلمة المرور الحالية");
            return;
        }
        setSavingEmail(true);
        try {
            await api.put("/auth/profile/email", {
                current_password: emailPwd,
                new_email: newEmail.trim(),
            });
            toast.success("تم تحديث البريد الإلكتروني بنجاح");
            setNewEmail("");
            setEmailPwd("");
            await refreshUser?.();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setSavingEmail(false);
        }
    };

    const submitPassword = async (e) => {
        e.preventDefault();
        if (newPwd.length < 6) {
            toast.error("كلمة المرور الجديدة يجب أن تكون 6 أحرف على الأقل");
            return;
        }
        if (newPwd !== confirmPwd) {
            toast.error("كلمتا المرور غير متطابقتين");
            return;
        }
        setSavingPwd(true);
        try {
            await api.put("/auth/profile/password", {
                current_password: curPwd,
                new_password: newPwd,
            });
            toast.success("تم تحديث كلمة المرور بنجاح");
            setCurPwd("");
            setNewPwd("");
            setConfirmPwd("");
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setSavingPwd(false);
        }
    };

    const submitSecurity = async (e) => {
        e.preventDefault();
        if (question.trim().length < 4 || answer.trim().length < 2 || !secPwd) {
            toast.error("الرجاء تعبئة السؤال (4+ أحرف)، الإجابة (حرفين+)، وكلمة المرور الحالية");
            return;
        }
        setSavingSec(true);
        try {
            await api.put("/auth/profile/security-question", {
                current_password: secPwd,
                question: question.trim(),
                answer: answer.trim(),
            });
            toast.success("تم حفظ سؤال الأمان بنجاح");
            setSecPwd("");
            setAnswer("");
            await refreshUser?.();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setSavingSec(false);
        }
    };

    return (
        <div className="max-w-3xl mx-auto space-y-6" data-testid="profile-page">
            <header className="space-y-1">
                <h1 className="text-3xl sm:text-4xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                    حسابي
                </h1>
                <p className="text-muted-foreground">إدارة بيانات حسابك الشخصية وأمانه.</p>
                {user?.is_owner && (
                    <span className="inline-flex items-center gap-1.5 mt-2 px-2.5 py-1 rounded-full bg-amber-100 text-amber-800 text-xs font-bold" data-testid="profile-owner-badge">
                        <ShieldCheck size={14} weight="fill" /> Owner — مالك الحساب
                    </span>
                )}
            </header>

            {/* Name */}
            <Section title="الاسم" subtitle="الاسم الذي يظهر في القائمة الجانبية." icon={UserCircle} testid="profile-name-section">
                <form onSubmit={submitName} className="space-y-4">
                    <Field label="الاسم" testid="profile-name-field">
                        <input
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            className={inputCls}
                            maxLength={80}
                            data-testid="profile-name-input"
                        />
                    </Field>
                    <SaveButton busy={savingName} testid="profile-name-save-btn" />
                </form>
            </Section>

            {/* Email */}
            <Section title="البريد الإلكتروني" subtitle={`الحالي: ${user?.email || "—"}`} icon={EnvelopeSimple} testid="profile-email-section">
                <form onSubmit={submitEmail} className="space-y-4">
                    <Field label="البريد الإلكتروني الجديد" testid="profile-email-new-field">
                        <input
                            type="email"
                            value={newEmail}
                            onChange={(e) => setNewEmail(e.target.value)}
                            className={inputCls}
                            placeholder="you@example.com"
                            data-testid="profile-email-new-input"
                            dir="ltr"
                            style={{ textAlign: "right" }}
                        />
                    </Field>
                    <Field label="كلمة المرور الحالية" testid="profile-email-pwd-field">
                        <PasswordInput
                            value={emailPwd}
                            onChange={(e) => setEmailPwd(e.target.value)}
                            placeholder="للتأكيد فقط"
                            testid="profile-email-pwd-input"
                        />
                    </Field>
                    <SaveButton busy={savingEmail} testid="profile-email-save-btn" children="تحديث البريد" />
                </form>
            </Section>

            {/* Password */}
            <Section title="كلمة المرور" subtitle="غيّر كلمة مرورك بانتظام للحفاظ على أمان حسابك." icon={LockKey} testid="profile-password-section">
                <form onSubmit={submitPassword} className="space-y-4">
                    <Field label="كلمة المرور الحالية" testid="profile-cur-pwd-field">
                        <PasswordInput
                            value={curPwd}
                            onChange={(e) => setCurPwd(e.target.value)}
                            placeholder="••••••••"
                            testid="profile-cur-pwd-input"
                        />
                    </Field>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <Field label="كلمة المرور الجديدة" testid="profile-new-pwd-field">
                            <PasswordInput
                                value={newPwd}
                                onChange={(e) => setNewPwd(e.target.value)}
                                placeholder="6 أحرف على الأقل"
                                testid="profile-new-pwd-input"
                            />
                        </Field>
                        <Field label="تأكيد كلمة المرور الجديدة" testid="profile-confirm-pwd-field">
                            <PasswordInput
                                value={confirmPwd}
                                onChange={(e) => setConfirmPwd(e.target.value)}
                                placeholder="أعد كتابتها"
                                testid="profile-confirm-pwd-input"
                            />
                        </Field>
                    </div>
                    <SaveButton busy={savingPwd} testid="profile-pwd-save-btn" children="تحديث كلمة المرور" />
                </form>
            </Section>

            {/* Security Question */}
            <Section
                title="سؤال الأمان"
                subtitle="يُستخدم لاسترجاع كلمة المرور إذا نسيتها."
                icon={ShieldCheck}
                testid="profile-security-section"
            >
                {user?.has_security_question && (
                    <div className="mb-4 p-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm text-emerald-900" data-testid="profile-security-active-banner">
                        ✓ سؤال الأمان مفعّل. يمكنك تحديث السؤال/الإجابة في أي وقت.
                    </div>
                )}
                <form onSubmit={submitSecurity} className="space-y-4">
                    <Field label="السؤال" testid="profile-sec-question-field">
                        <input
                            value={question}
                            onChange={(e) => setQuestion(e.target.value)}
                            className={inputCls}
                            placeholder="مثال: ما اسم أول مدرسة دخلتها؟"
                            maxLength={200}
                            data-testid="profile-sec-question-input"
                        />
                    </Field>
                    <Field label="الإجابة" testid="profile-sec-answer-field">
                        <input
                            value={answer}
                            onChange={(e) => setAnswer(e.target.value)}
                            className={inputCls}
                            placeholder="إجابة تتذكرها"
                            maxLength={200}
                            data-testid="profile-sec-answer-input"
                        />
                        <p className="text-xs text-muted-foreground mt-1">سيتم تشفير الإجابة عند الحفظ. الفروق في المسافات وحالة الأحرف لا تؤثر على التحقق.</p>
                    </Field>
                    <Field label="كلمة المرور الحالية" testid="profile-sec-pwd-field">
                        <PasswordInput
                            value={secPwd}
                            onChange={(e) => setSecPwd(e.target.value)}
                            placeholder="للتأكيد فقط"
                            testid="profile-sec-pwd-input"
                        />
                    </Field>
                    <SaveButton busy={savingSec} testid="profile-sec-save-btn" children={user?.has_security_question ? "تحديث السؤال" : "حفظ السؤال"} />
                </form>
            </Section>
        </div>
    );
}
