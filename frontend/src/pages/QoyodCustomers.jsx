import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

function errorMessage(e, fallback) {
    const detail = e?.response?.data?.detail;

    if (typeof detail === "string") return detail;

    if (Array.isArray(detail)) {
        return detail
            .map((x) => x?.msg || x?.message || JSON.stringify(x))
            .filter(Boolean)
            .join(" ");
    }

    if (detail && typeof detail === "object") {
        return detail.message || detail.msg || detail.code || JSON.stringify(detail);
    }

    return e?.message || fallback;
}

function hasValue(value) {
    return value !== null && value !== undefined && String(value).trim() !== "";
}

function valuePreview(value) {
    return hasValue(value) ? String(value) : "—";
}

function Badge({ ok, children }) {
    return (
        <span
            className={`inline-flex items-center rounded-full px-2 py-1 text-xs font-bold border ${
                ok
                    ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                    : "bg-red-50 text-red-700 border-red-200"
            }`}
        >
            {children}
        </span>
    );
}

function Card({ title, value, hint, tone = "white" }) {
    const tones = {
        white: "bg-white border-slate-200",
        green: "bg-emerald-50 border-emerald-200",
        red: "bg-red-50 border-red-200",
        amber: "bg-amber-50 border-amber-200",
        blue: "bg-blue-50 border-blue-200",
    };

    return (
        <div className={`rounded-xl border p-4 ${tones[tone] || tones.white}`}>
            <p className="text-xs font-bold text-slate-600">{title}</p>
            <div className="text-2xl font-extrabold mt-1 num">{value}</div>
            {hint && <p className="text-xs text-slate-500 mt-2 leading-5">{hint}</p>}
        </div>
    );
}

function Field({ label, value, onChange, required = false, dir = "rtl", placeholder, testid }) {
    return (
        <label className="text-xs font-bold text-slate-600">
            {label}
            {required && <span className="text-red-600"> *</span>}
            <input
                value={value}
                onChange={(e) => onChange(e.target.value)}
                required={required}
                dir={dir}
                placeholder={placeholder}
                className="block mt-1 w-full border rounded-lg px-3 py-2 text-sm"
                data-testid={testid}
            />
        </label>
    );
}

export default function QoyodCustomers() {
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [settings, setSettings] = useState(null);
    const [health, setHealth] = useState(null);
    const [lastResult, setLastResult] = useState(null);

    const [form, setForm] = useState({
        lookup_kind: "phone",
        lookup_key: "",
        qoyod_contact_id: "",
        qoyod_contact_name: "",
        note: "",
    });

    const load = async () => {
        setLoading(true);

        try {
            const [settingsRes, healthRes] = await Promise.all([
                api.get("/integrations/qoyod/settings"),
                api.get("/integrations/qoyod/health"),
            ]);

            setSettings(settingsRes.data || {});
            setHealth(healthRes.data || {});
        } catch (e) {
            toast.error(errorMessage(e, "فشل تحميل صفحة عملاء قيود"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, []);

    const defaultCustomerConfigured = hasValue(settings?.default_customer_id);

    const formHelp = useMemo(() => {
        if (form.lookup_kind === "phone") {
            return {
                label: "رقم الجوال / lookup key",
                placeholder: "مثال: 0551234567 أو +966551234567",
                hint: "سيقوم Backend بتطبيع الرقم إلى صيغة +966 عند الحفظ إذا كان رقم سعودي.",
            };
        }

        return {
            label: "الإيميل / lookup key",
            placeholder: "customer@example.com",
            hint: "سيتم حفظ الإيميل بحروف صغيرة حتى يطابق مشتريات العميل القادمة.",
        };
    }, [form.lookup_kind]);

    const submitAdoption = async (e) => {
        e.preventDefault();

        if (!form.lookup_key.trim() || !form.qoyod_contact_id.trim()) {
            toast.error("أدخل lookup key ورقم العميل الحقيقي في قيود");
            return;
        }

        setSaving(true);
        setLastResult(null);

        try {
            const res = await api.post("/integrations/qoyod/customers/adopt", {
                lookup_key: form.lookup_key.trim(),
                lookup_kind: form.lookup_kind,
                qoyod_contact_id: form.qoyod_contact_id.trim(),
                qoyod_contact_name: form.qoyod_contact_name.trim() || undefined,
                note: form.note.trim() || undefined,
            });

            setLastResult(res.data || null);
            toast.success("تم اعتماد ربط العميل محلياً في ميزان");

            setForm({
                lookup_kind: form.lookup_kind,
                lookup_key: "",
                qoyod_contact_id: "",
                qoyod_contact_name: "",
                note: "",
            });
        } catch (e) {
            toast.error(errorMessage(e, "فشل اعتماد العميل"));
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-6" dir="rtl" data-testid="qoyod-customers-page">
            <div className="flex items-start justify-between flex-wrap gap-4">
                <div>
                    <div className="inline-flex items-center gap-2 rounded-full bg-slate-900 text-white px-3 py-1 text-xs font-bold mb-3">
                        👥 Qoyod Customers · Local Mapping
                    </div>

                    <h1 className="text-3xl font-extrabold text-slate-900">عملاء قيود</h1>

                    <p className="text-sm text-slate-600 mt-2 leading-7 max-w-3xl">
                        هذه الصفحة تعتمد ربط العميل الموجود في قيود مع رقم جوال أو إيميل من سلة/ميزان.
                        الاعتماد هنا محلي داخل ميزان فقط ولا يرسل طلب إنشاء أو تعديل إلى قيود.
                    </p>
                </div>

                <button
                    type="button"
                    onClick={load}
                    disabled={loading}
                    className="rounded-lg bg-slate-900 text-white px-4 py-2 text-sm font-bold disabled:opacity-50"
                    data-testid="qoyod-customers-refresh"
                >
                    {loading ? "جارٍ التحميل…" : "🔄 تحديث"}
                </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                <Card
                    title="حالة اتصال قيود"
                    value={health?.credentials_loaded ? "متصل" : "غير متصل"}
                    tone={health?.credentials_loaded ? "green" : "red"}
                />

                <Card
                    title="Default Customer"
                    value={defaultCustomerConfigured ? "موجود" : "غير مضبوط"}
                    tone={defaultCustomerConfigured ? "green" : "amber"}
                    hint={`default_customer_id = ${valuePreview(settings?.default_customer_id)}`}
                />

                <Card
                    title="طريقة الاعتماد"
                    value="Local-only"
                    tone="blue"
                    hint="لا تستدعي Qoyod API؛ تحفظ الربط داخل ميزان فقط."
                />

                <Card
                    title="Dry flag"
                    value="false"
                    tone="green"
                    hint="اعتماد العميل يمسح dry_run_only حتى يصبح الربط حقيقياً."
                />
            </div>

            <div className="rounded-xl border bg-emerald-50 border-emerald-200 p-4">
                <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div>
                        <h2 className="text-lg font-extrabold text-emerald-900">
                            اعتماد العميل لا ينشئ عميلاً في قيود
                        </h2>
                        <p className="text-sm text-emerald-800 mt-1 leading-6">
                            استخدم هذه الصفحة فقط عندما تكون متأكد أن العميل موجود في قيود ولديك رقمه الحقيقي.
                            بعدها سيستخدم ميزان هذا الربط في الطلبات القادمة لنفس الجوال أو الإيميل.
                        </p>
                    </div>

                    <Badge ok>✅ آمن محلياً</Badge>
                </div>
            </div>

            <form
                onSubmit={submitAdoption}
                className="rounded-xl border bg-white p-5 space-y-4"
                data-testid="qoyod-customer-adopt-form"
            >
                <div>
                    <h2 className="text-xl font-extrabold">اعتماد ربط عميل موجود في قيود</h2>

                    <p className="text-sm text-slate-600 mt-1 leading-6">
                        اختر نوع المفتاح، ثم أدخل رقم الجوال أو الإيميل، وأدخل رقم العميل الحقيقي من قيود.
                    </p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
                    <label className="text-xs font-bold text-slate-600">
                        نوع المفتاح
                        <select
                            value={form.lookup_kind}
                            onChange={(e) =>
                                setForm((f) => ({
                                    ...f,
                                    lookup_kind: e.target.value,
                                    lookup_key: "",
                                }))
                            }
                            className="block mt-1 w-full border rounded-lg px-3 py-2 text-sm"
                            data-testid="adopt-lookup-kind"
                        >
                            <option value="phone">جوال</option>
                            <option value="email">إيميل</option>
                        </select>
                    </label>

                    <Field
                        label={formHelp.label}
                        value={form.lookup_key}
                        onChange={(v) => setForm((f) => ({ ...f, lookup_key: v }))}
                        required
                        dir="ltr"
                        placeholder={formHelp.placeholder}
                        testid="adopt-lookup-key"
                    />

                    <Field
                        label="Qoyod contact id"
                        value={form.qoyod_contact_id}
                        onChange={(v) => setForm((f) => ({ ...f, qoyod_contact_id: v }))}
                        required
                        dir="ltr"
                        placeholder="مثال: 12345"
                        testid="adopt-qoyod-contact-id"
                    />

                    <Field
                        label="اسم العميل في قيود"
                        value={form.qoyod_contact_name}
                        onChange={(v) => setForm((f) => ({ ...f, qoyod_contact_name: v }))}
                        placeholder="اختياري"
                        testid="adopt-qoyod-contact-name"
                    />

                    <Field
                        label="ملاحظة"
                        value={form.note}
                        onChange={(v) => setForm((f) => ({ ...f, note: v }))}
                        placeholder="اختياري"
                        testid="adopt-note"
                    />
                </div>

                <div className="rounded-lg border bg-slate-50 p-3 text-xs text-slate-600 leading-6">
                    {formHelp.hint}
                </div>

                <div className="flex items-center gap-3 flex-wrap">
                    <button
                        type="submit"
                        disabled={saving}
                        className="rounded-lg bg-slate-900 text-white px-4 py-2 text-sm font-bold disabled:opacity-50"
                    >
                        {saving ? "جارٍ الاعتماد…" : "اعتماد ربط العميل"}
                    </button>

                    <span className="text-xs text-slate-500">
                        سيتم تحديث جدول الربط المحلي `qoyod_customers_mapping` فقط.
                    </span>
                </div>
            </form>

            {lastResult && (
                <div className="rounded-xl border bg-emerald-50 border-emerald-200 p-5">
                    <h2 className="text-xl font-extrabold text-emerald-900 mb-3">
                        ✅ آخر اعتماد ناجح
                    </h2>

                    <div className="grid grid-cols-1 md:grid-cols-4 gap-3 text-sm">
                        <div className="rounded-lg bg-white border p-3">
                            <div className="text-xs text-slate-500">lookup_kind</div>
                            <div className="font-bold mt-1">{valuePreview(lastResult.lookup_kind)}</div>
                        </div>

                        <div className="rounded-lg bg-white border p-3">
                            <div className="text-xs text-slate-500">lookup_key</div>
                            <div className="font-bold mt-1 num" dir="ltr">
                                {valuePreview(lastResult.lookup_key)}
                            </div>
                        </div>

                        <div className="rounded-lg bg-white border p-3">
                            <div className="text-xs text-slate-500">qoyod_contact_id</div>
                            <div className="font-bold mt-1 num" dir="ltr">
                                {valuePreview(lastResult.qoyod_contact_id)}
                            </div>
                        </div>

                        <div className="rounded-lg bg-white border p-3">
                            <div className="text-xs text-slate-500">dry_run_only</div>
                            <div className="font-bold mt-1">
                                {String(lastResult.dry_run_only)}
                            </div>
                        </div>
                    </div>
                </div>
            )}

            <div className="rounded-xl border bg-white p-5">
                <h2 className="text-xl font-extrabold">متى أستخدم هذه الصفحة؟</h2>

                <ul className="list-disc list-inside text-sm text-slate-700 leading-7 mt-3">
                    <li>عندما يفشل طلب لأن العميل يحتاج ربطاً حقيقياً مع قيود.</li>
                    <li>عندما يكون لديك رقم العميل الحقيقي من قيود وتريد ربطه بجوال أو إيميل من سلة.</li>
                    <li>عندما تريد تحويل mapping سابق من Dry-run إلى ربط حقيقي داخل ميزان.</li>
                    <li>لا تستخدمها لإنشاء عميل جديد في قيود؛ الإنشاء يتم عبر Pipeline أو يدوياً داخل قيود.</li>
                </ul>
            </div>
        </div>
    );
}