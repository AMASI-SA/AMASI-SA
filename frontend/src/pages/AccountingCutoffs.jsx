/**
 * Iter-149 — Per-provider Accounting Cutoff Settings page.
 *
 * Lets the merchant set a different "accounting start date" per
 * provider (Tabby / Tamara / Salla / COD / Bank Transfer).  Any entity
 * dated before its provider's cutoff is treated as Archived /
 * Pre-Accounting — excluded from profits, settlements, balances,
 * bank matching, and reports, but still searchable.
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const PROVIDER_META = {
    tabby:         { label: "Tabby",          color: "bg-emerald-50 border-emerald-200" },
    tamara:        { label: "Tamara",         color: "bg-rose-50 border-rose-200" },
    salla:         { label: "Salla",          color: "bg-indigo-50 border-indigo-200" },
    cod:           { label: "الدفع عند الاستلام (COD)", color: "bg-amber-50 border-amber-200" },
    bank_transfer: { label: "التحويل البنكي", color: "bg-sky-50 border-sky-200" },
};

export default function AccountingCutoffs() {
    const [cutoffs, setCutoffs] = useState({});
    const [defaults, setDefaults] = useState({});
    const [draft, setDraft] = useState({});
    const [saving, setSaving] = useState({});
    const [recomputing, setRecomputing] = useState(false);
    const [loading, setLoading] = useState(true);

    const token = () => localStorage.getItem("token") || "";
    const headers = () => ({ Authorization: `Bearer ${token()}` });

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await axios.get(`${API}/accounting/cutoffs`, { headers: headers() });
            setCutoffs(data.cutoffs || {});
            setDefaults(data.defaults || {});
            setDraft(data.cutoffs || {});
        } catch (e) {
            toast.error("فشل تحميل تواريخ بدء المحاسبة");
        } finally {
            setLoading(false);
        }
    };
    useEffect(() => { load(); }, []);

    const saveOne = async (provider) => {
        const date = draft[provider];
        if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
            toast.error("صيغة التاريخ يجب أن تكون YYYY-MM-DD");
            return;
        }
        setSaving((s) => ({ ...s, [provider]: true }));
        try {
            const { data } = await axios.put(
                `${API}/accounting/cutoffs/${provider}`,
                { accounting_start_date: date },
                { headers: headers() },
            );
            if (data.changed) {
                toast.success(`تحديث ${PROVIDER_META[provider]?.label}: ${data.old} → ${data.new}`);
                setCutoffs((c) => ({ ...c, [provider]: data.new }));
            } else {
                toast.info("لا تغيير");
            }
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل الحفظ");
        } finally {
            setSaving((s) => ({ ...s, [provider]: false }));
        }
    };

    const recomputeAll = async () => {
        setRecomputing(true);
        try {
            const { data } = await axios.post(
                `${API}/accounting/cutoffs/recompute`,
                {},
                { headers: headers() },
            );
            const totals = Object.values(data.results || {}).reduce(
                (s, r) => ({
                    txn: s.txn + (r.payment_transactions || 0),
                    ref: s.ref + (r.payment_refunds || 0),
                    ent: s.ent + (r.settlement_entries || 0),
                    ord: s.ord + (r.unified_orders || 0),
                    bnk: s.bnk + (r.account_transactions || 0),
                }),
                { txn: 0, ref: 0, ent: 0, ord: 0, bnk: 0 },
            );
            toast.success(
                `إعادة الاحتساب: ${totals.txn} معاملة + ${totals.ref} استرجاع + ${totals.ent} كشف + ${totals.ord} طلب + ${totals.bnk} حركة بنك مُؤرشفة`,
            );
        } catch (e) {
            toast.error(e?.response?.data?.detail || "فشل إعادة الاحتساب");
        } finally {
            setRecomputing(false);
        }
    };

    if (loading) {
        return <div className="p-8 text-center text-slate-500">جاري التحميل…</div>;
    }

    const providers = ["tabby", "tamara", "salla", "cod", "bank_transfer"];
    return (
        <div className="max-w-4xl mx-auto p-6 space-y-6" dir="rtl">
            <div className="space-y-2">
                <h1 className="text-3xl font-bold text-slate-900">تواريخ بدء المحاسبة</h1>
                <p className="text-sm text-slate-600">
                    تحدد لكل مزود التاريخ الذي يبدأ منه النظام في احتساب التسويات، الأرباح،
                    المطابقات، والمستحقات. أي طلب أو حركة قبل هذا التاريخ تُعتبر مؤرشفة
                    محاسبياً (Pre-Accounting) — تبقى محفوظة وقابلة للبحث لكن لا تدخل في أي
                    حساب مالي.
                </p>
            </div>

            <Card className="border-slate-200">
                <CardHeader>
                    <CardTitle className="text-lg flex items-center justify-between">
                        <span>المزودون والتواريخ</span>
                        <Button
                            data-testid="recompute-all-btn"
                            onClick={recomputeAll}
                            disabled={recomputing}
                            className="bg-indigo-600 hover:bg-indigo-700"
                        >
                            {recomputing ? "جاري إعادة الاحتساب…" : "🔄 إعادة احتساب جميع التقارير"}
                        </Button>
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    {providers.map((p) => {
                        const meta = PROVIDER_META[p] || { label: p, color: "" };
                        const cur = cutoffs[p] || "";
                        const def = defaults[p] || "";
                        const isDefault = cur === def;
                        return (
                            <div
                                key={p}
                                className={`flex items-center gap-4 p-4 rounded-lg border ${meta.color}`}
                                data-testid={`cutoff-row-${p}`}
                            >
                                <div className="w-48 font-bold text-slate-900">
                                    {meta.label}
                                </div>
                                <div className="flex-1">
                                    <Label className="text-xs text-slate-500 mb-1 block">
                                        تاريخ بدء المحاسبة
                                    </Label>
                                    <Input
                                        type="date"
                                        value={draft[p] || ""}
                                        onChange={(e) => setDraft({ ...draft, [p]: e.target.value })}
                                        data-testid={`cutoff-input-${p}`}
                                        className="font-mono"
                                    />
                                    <div className="text-[10px] text-slate-500 mt-1">
                                        الافتراضي: <span className="font-mono">{def}</span>
                                        {isDefault && (
                                            <span className="mr-2 px-2 py-0.5 bg-slate-200 rounded text-slate-700">
                                                افتراضي
                                            </span>
                                        )}
                                    </div>
                                </div>
                                <Button
                                    data-testid={`cutoff-save-${p}`}
                                    onClick={() => saveOne(p)}
                                    disabled={saving[p] || draft[p] === cur}
                                    variant="outline"
                                >
                                    {saving[p] ? "…" : "حفظ"}
                                </Button>
                            </div>
                        );
                    })}
                </CardContent>
            </Card>

            <Card className="border-amber-200 bg-amber-50">
                <CardContent className="p-4 text-sm text-amber-900">
                    💡 <strong>ملاحظة:</strong> بعد تغيير أي تاريخ، التسويات والمطابقات تطبّق
                    الفلتر فوراً تلقائياً. زر "إعادة احتساب جميع التقارير" يُعلِّم الطلبات
                    والحركات القديمة بـ <code className="font-mono bg-amber-100 px-1 rounded">is_pre_accounting</code>{" "}
                    حتى تخرج تماماً من باقي التقارير (الأرباح والمركز المالي).
                </CardContent>
            </Card>
        </div>
    );
}
