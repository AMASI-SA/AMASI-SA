import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

const REQUIRED_DEFAULTS = [
    ["default_product_category_id", "التصنيف", "category_id"],
    ["default_product_tax_id", "الضريبة", "tax_id"],
    ["default_product_unit_type_id", "وحدة القياس", "product_unit_type_id"],
    ["default_sales_account_id", "حساب المبيعات", "sales_account_id"],
];

const fmtInt = (n) => (Number(n) || 0).toLocaleString("en-US");

function hasValue(value) {
    if (value === null || value === undefined) return false;
    if (Array.isArray(value)) return value.some((x) => hasValue(x));
    return String(value).trim() !== "";
}

function valuePreview(value) {
    if (!hasValue(value)) return "—";
    if (Array.isArray(value)) return value.filter((x) => hasValue(x)).join(", ");
    return String(value);
}

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
        return detail.message || detail.msg || JSON.stringify(detail);
    }

    return e?.message || fallback;
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

function Field({ label, value, onChange, required = false, dir = "rtl", testid }) {
    return (
        <label className="text-xs font-bold text-slate-600">
            {label}
            {required && <span className="text-red-600"> *</span>}
            <input
                value={value}
                onChange={(e) => onChange(e.target.value)}
                required={required}
                dir={dir}
                className="block mt-1 w-full border rounded-lg px-3 py-2 text-sm"
                data-testid={testid}
            />
        </label>
    );
}

export default function QoyodProducts() {
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [settings, setSettings] = useState(null);
    const [health, setHealth] = useState(null);
    const [dryMappings, setDryMappings] = useState([]);
    const [query, setQuery] = useState("");

    const [form, setForm] = useState({
        sku: "",
        qoyod_product_id: "",
        qoyod_product_name: "",
        note: "",
    });

    const load = async () => {
        setLoading(true);

        try {
            const [settingsRes, healthRes, dryRes] = await Promise.all([
                api.get("/integrations/qoyod/settings"),
                api.get("/integrations/qoyod/health"),
                api.get("/integrations/qoyod/admin/products/dry-mappings?limit=2000"),
            ]);

            setSettings(settingsRes.data || {});
            setHealth(healthRes.data || {});
            setDryMappings(dryRes.data?.items || []);
        } catch (e) {
            toast.error(errorMessage(e, "فشل تحميل منتجات قيود"));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
    }, []);

    const defaults = useMemo(() => {
        const s = settings || {};

        return REQUIRED_DEFAULTS.map(([key, label, apiKey]) => ({
            key,
            label,
            apiKey,
            value: s[key],
            ok: hasValue(s[key]),
        }));
    }, [settings]);

    const defaultsOk = defaults.every((x) => x.ok);

    const filteredDryMappings = useMemo(() => {
        const q = query.trim().toLowerCase();

        if (!q) return dryMappings;

        return dryMappings.filter((row) =>
            [
                row.sku,
                row.qoyod_product_id,
                row.qoyod_product_name,
                row.reason,
                row.source,
            ].some((v) => String(v || "").toLowerCase().includes(q))
        );
    }, [dryMappings, query]);

    const fillFromRow = (row) => {
        const currentId = String(row.qoyod_product_id || "");

        setForm({
            sku: row.sku || "",
            qoyod_product_id:
                currentId.startsWith("DRY:") || currentId.startsWith("PREVIEW:")
                    ? ""
                    : currentId,
            qoyod_product_name: row.qoyod_product_name || "",
            note: `Repair mapping for ${row.sku || "SKU"}`,
        });

        window.scrollTo({ top: 0, behavior: "smooth" });
    };

    const submitAdoption = async (e) => {
        e.preventDefault();

        if (!form.sku.trim() || !form.qoyod_product_id.trim()) {
            toast.error("أدخل SKU ورقم المنتج الحقيقي في قيود");
            return;
        }

        setSaving(true);

        try {
            await api.post("/integrations/qoyod/products/adopt", {
                sku: form.sku.trim(),
                qoyod_product_id: form.qoyod_product_id.trim(),
                qoyod_product_name: form.qoyod_product_name.trim() || undefined,
                note: form.note.trim() || undefined,
            });

            toast.success("تم اعتماد ربط المنتج محلياً في ميزان");

            setForm({
                sku: "",
                qoyod_product_id: "",
                qoyod_product_name: "",
                note: "",
            });

            await load();
        } catch (e) {
            toast.error(errorMessage(e, "فشل اعتماد المنتج"));
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-6" dir="rtl" data-testid="qoyod-products-page">
            <div className="flex items-start justify-between flex-wrap gap-4">
                <div>
                    <div className="inline-flex items-center gap-2 rounded-full bg-slate-900 text-white px-3 py-1 text-xs font-bold mb-3">
                        📦 Qoyod Products · Local Mapping
                    </div>

                    <h1 className="text-3xl font-extrabold text-slate-900">منتجات قيود</h1>

                    <p className="text-sm text-slate-600 mt-2 leading-7 max-w-3xl">
                        هذه الصفحة تدير ربط SKU بين سلة/ميزان ومنتجات قيود.
                        الاعتماد هنا محلي داخل ميزان فقط ولا يرسل طلب إنشاء أو تعديل إلى قيود.
                    </p>
                </div>

                <button
                    type="button"
                    onClick={load}
                    disabled={loading}
                    className="rounded-lg bg-slate-900 text-white px-4 py-2 text-sm font-bold disabled:opacity-50"
                    data-testid="qoyod-products-refresh"
                >
                    {loading ? "جارٍ التحميل…" : "🔄 تحديث"}
                </button>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Card
                    title="حالة اتصال قيود"
                    value={health?.credentials_loaded ? "متصل" : "غير متصل"}
                    tone={health?.credentials_loaded ? "green" : "red"}
                />

                <Card
                    title="إعدادات المنتج"
                    value={defaultsOk ? "مكتملة" : "ناقصة"}
                    tone={defaultsOk ? "green" : "red"}
                    hint="التصنيف، الضريبة، الوحدة، حساب المبيعات"
                />

                <Card
                    title="Mappings تحتاج إصلاح"
                    value={fmtInt(dryMappings.length)}
                    tone={dryMappings.length ? "amber" : "green"}
                    hint="DRY/PREVIEW أو dry_run_only"
                />

                <Card
                    title="نوع المنتج الافتراضي"
                    value={settings?.default_product_type || health?.default_product_type || "—"}
                    tone="blue"
                />
            </div>

            <div
                className={`rounded-xl border p-4 ${
                    defaultsOk
                        ? "bg-emerald-50 border-emerald-200"
                        : "bg-red-50 border-red-200"
                }`}
            >
                <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div>
                        <h2 className="text-lg font-extrabold">فحص إعدادات إنشاء المنتجات</h2>
                        <p className="text-sm text-slate-600 mt-1 leading-6">
                            إذا نقص أي حقل من هذه الحقول، إنشاء المنتجات الجديدة في قيود سيتوقف قبل الإرسال.
                        </p>
                    </div>

                    <Badge ok={defaultsOk}>{defaultsOk ? "✅ جاهز" : "⛔ ناقص"}</Badge>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mt-4">
                    {defaults.map((row) => (
                        <div key={row.key} className="rounded-lg bg-white border p-3">
                            <div className="flex items-center justify-between gap-2">
                                <span className="font-bold text-sm">{row.label}</span>
                                <Badge ok={row.ok}>{row.ok ? "موجود" : "ناقص"}</Badge>
                            </div>

                            <div className="text-xs text-slate-500 mt-1" dir="ltr">
                                {row.apiKey}
                            </div>

                            <div className="text-sm font-bold mt-2 break-words num">
                                {valuePreview(row.value)}
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            <form
                onSubmit={submitAdoption}
                className="rounded-xl border bg-white p-5 space-y-4"
                data-testid="qoyod-product-adopt-form"
            >
                <div>
                    <h2 className="text-xl font-extrabold">اعتماد ربط منتج موجود في قيود</h2>

                    <p className="text-sm text-slate-600 mt-1 leading-6">
                        استخدمها عندما يكون المنتج موجوداً في قيود فعلاً وتريد ربطه بـ SKU من سلة.
                        لا تضع رقم DRY أو PREVIEW هنا؛ أدخل رقم المنتج الحقيقي من قيود.
                    </p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                    <Field
                        label="SKU"
                        value={form.sku}
                        onChange={(v) => setForm((f) => ({ ...f, sku: v }))}
                        required
                        testid="adopt-sku"
                    />

                    <Field
                        label="Qoyod product id"
                        value={form.qoyod_product_id}
                        onChange={(v) => setForm((f) => ({ ...f, qoyod_product_id: v }))}
                        required
                        dir="ltr"
                        testid="adopt-qoyod-id"
                    />

                    <Field
                        label="اسم المنتج في قيود"
                        value={form.qoyod_product_name}
                        onChange={(v) => setForm((f) => ({ ...f, qoyod_product_name: v }))}
                        testid="adopt-name"
                    />

                    <Field
                        label="ملاحظة"
                        value={form.note}
                        onChange={(v) => setForm((f) => ({ ...f, note: v }))}
                        testid="adopt-note"
                    />
                </div>

                <div className="flex items-center gap-3 flex-wrap">
                    <button
                        type="submit"
                        disabled={saving}
                        className="rounded-lg bg-slate-900 text-white px-4 py-2 text-sm font-bold disabled:opacity-50"
                    >
                        {saving ? "جارٍ الاعتماد…" : "اعتماد الربط"}
                    </button>

                    <span className="text-xs text-slate-500">
                        عملية محلية داخل ميزان فقط، ولا تستدعي Qoyod API.
                    </span>
                </div>
            </form>

            <div className="rounded-xl border bg-white overflow-hidden">
                <div className="p-5 border-b flex items-center justify-between gap-3 flex-wrap">
                    <div>
                        <h2 className="text-xl font-extrabold">Mappings تحتاج إصلاح</h2>
                        <p className="text-sm text-slate-500 mt-1">
                            تعرض الصفوف التي تحمل DRY/PREVIEW أو dry_run_only=true.
                        </p>
                    </div>

                    <input
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        placeholder="بحث SKU / product id / name"
                        className="border rounded-lg px-3 py-2 text-sm min-w-[260px]"
                        data-testid="qoyod-products-search"
                    />
                </div>

                {loading ? (
                    <div className="p-10 text-center text-slate-500">جارٍ التحميل…</div>
                ) : filteredDryMappings.length === 0 ? (
                    <div className="p-10 text-center">
                        <div className="text-5xl mb-3">✅</div>
                        <div className="font-extrabold text-slate-800">
                            لا توجد mappings جافة تحتاج إصلاح
                        </div>
                        <p className="text-sm text-slate-500 mt-2">
                            إذا ظهر طلب متوقف بسبب منتج، سيظهر هنا عند وجود DRY/PREVIEW mapping.
                        </p>
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="min-w-full text-sm">
                            <thead className="bg-slate-50 text-slate-600">
                                <tr>
                                    <th className="p-3 text-right">SKU</th>
                                    <th className="p-3 text-right">Qoyod product id الحالي</th>
                                    <th className="p-3 text-right">اسم المنتج</th>
                                    <th className="p-3 text-right">السبب</th>
                                    <th className="p-3 text-right">المصدر</th>
                                    <th className="p-3 text-right">إجراء</th>
                                </tr>
                            </thead>

                            <tbody>
                                {filteredDryMappings.map((row) => (
                                    <tr
                                        key={`${row.sku}-${row.qoyod_product_id}`}
                                        className="border-t hover:bg-slate-50"
                                    >
                                        <td className="p-3 font-extrabold num" dir="ltr">
                                            {row.sku || "—"}
                                        </td>

                                        <td className="p-3 num" dir="ltr">
                                            {row.qoyod_product_id || "—"}
                                        </td>

                                        <td className="p-3">
                                            {row.qoyod_product_name || "—"}
                                        </td>

                                        <td className="p-3 text-amber-700 font-bold">
                                            {row.reason || "—"}
                                        </td>

                                        <td className="p-3">
                                            {row.source || "—"}
                                        </td>

                                        <td className="p-3">
                                            <button
                                                type="button"
                                                onClick={() => fillFromRow(row)}
                                                className="rounded-lg bg-blue-50 border border-blue-200 text-blue-700 px-3 py-1.5 text-xs font-bold hover:bg-blue-100"
                                            >
                                                تعبئة نموذج الاعتماد
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}