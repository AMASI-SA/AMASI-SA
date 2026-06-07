/**
 * OrderStatusPolicySection (Iter-83)
 * ----------------------------------
 * Settings card that lets the merchant categorise each observed order
 * status into one of: confirmed | pending | refunded | cancelled.
 *
 *  • confirmed → counted in /api/payment-gateway-metrics → net (assets)
 *  • pending   → tracked in pending_gross (NOT in net)
 *  • refunded  → booked as full refund (subtracts from net)
 *  • cancelled → excluded entirely
 *
 * All four categories drive every page through the same central endpoint.
 */
import { useEffect, useMemo, useState } from "react";
import { ListChecks, ArrowsClockwise, ArrowCounterClockwise, CheckCircle } from "@phosphor-icons/react";
import api from "../lib/api";
import { toast } from "sonner";

const CAT_BADGE = {
    confirmed: { label: "مؤكدة",  cls: "bg-emerald-100 text-emerald-800 border-emerald-200" },
    pending:   { label: "معلّقة", cls: "bg-amber-100 text-amber-800 border-amber-200"     },
    refunded:  { label: "مسترجعة", cls: "bg-rose-100 text-rose-800 border-rose-200"        },
    cancelled: { label: "ملغاة",  cls: "bg-slate-200 text-slate-700 border-slate-300"    },
};

function fmtMoney(n) {
    return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(n) || 0);
}

export default function OrderStatusPolicySection() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [overrides, setOverrides] = useState({}); // { status: category }

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/order-status-policy");
            setData(data);
            setOverrides({});
        } catch {
            toast.error("تعذّر تحميل سياسة حالات الطلبات");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const { data } = await api.get("/order-status-policy");
                if (!cancelled) {
                    setData(data);
                    setOverrides({});
                }
            } catch {
                if (!cancelled) toast.error("تعذّر تحميل سياسة حالات الطلبات");
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, []);

    const rows = data?.rows || [];
    const cats = data?.categories || [];

    const dirty = Object.keys(overrides).length > 0;

    const effective = (r) => overrides[r.status] ?? r.category;

    const counts = useMemo(() => {
        const out = { confirmed: 0, pending: 0, refunded: 0, cancelled: 0 };
        const sums = { confirmed: 0, pending: 0, refunded: 0, cancelled: 0 };
        for (const r of rows) {
            const c = effective(r);
            if (out[c] !== undefined) {
                out[c] += r.orders_count;
                sums[c] += r.total_amount || 0;
            }
        }
        return { counts: out, sums };
    }, [rows, overrides]);  

    const setCategory = (status, category) => {
        setOverrides((prev) => {
            const next = { ...prev };
            const row = rows.find((r) => r.status === status);
            if (row && row.category === category) {
                delete next[status];
            } else {
                next[status] = category;
            }
            return next;
        });
    };

    const save = async () => {
        const items = Object.entries(overrides).map(([status, category]) => ({ status, category }));
        if (items.length === 0) return;
        setSaving(true);
        try {
            await api.put("/order-status-policy", { items });
            toast.success(`تم حفظ ${items.length} تغيير. الأرقام محدَّثة في كل الصفحات.`);
            await load();
        } catch (e) {
            toast.error("تعذّر الحفظ");
        } finally {
            setSaving(false);
        }
    };

    const resetAll = async () => {
        if (!window.confirm("هل أنت متأكد من إعادة جميع الحالات للإفتراضي؟")) return;
        setSaving(true);
        try {
            await api.post("/order-status-policy/reset");
            toast.success("تمت إعادة الضبط للإفتراضي.");
            await load();
        } catch {
            toast.error("تعذّر إعادة الضبط");
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="rounded-xl border border-border bg-white p-6" data-testid="order-status-policy-section">
            <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
                <div className="flex items-start gap-3 min-w-0">
                    <div className="w-10 h-10 rounded-lg bg-indigo-100 text-indigo-700 flex items-center justify-center shrink-0">
                        <ListChecks size={22} weight="duotone" />
                    </div>
                    <div className="min-w-0">
                        <h2 className="text-xl sm:text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>
                            حالات الطلبات — اعتماد الأصول
                        </h2>
                        <p className="text-sm text-muted-foreground mt-1 max-w-2xl leading-relaxed">
                            صنّف كل حالة طلب إلى إحدى أربع فئات. هذا الإعداد ينعكس مباشرةً في «التقارير»،
                            «الأصول والحسابات»، «المطابقة» و«لوحة التحكم» عبر نقطة النهاية المركزية
                            <code className="mx-1 px-1 bg-slate-100 rounded">/api/payment-gateway-metrics</code>.
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        onClick={resetAll}
                        disabled={saving || loading}
                        className="px-3 py-1.5 rounded-lg text-xs font-bold text-slate-600 bg-slate-100 hover:bg-slate-200 inline-flex items-center gap-1 disabled:opacity-50"
                        data-testid="osp-reset-btn"
                    >
                        <ArrowCounterClockwise size={14} weight="bold" />
                        إعادة افتراضي
                    </button>
                    <button
                        type="button"
                        onClick={save}
                        disabled={!dirty || saving}
                        className="px-4 py-1.5 rounded-lg text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 inline-flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
                        data-testid="osp-save-btn"
                    >
                        {saving ? <ArrowsClockwise size={14} className="animate-spin" /> : <CheckCircle size={14} weight="bold" />}
                        حفظ ({Object.keys(overrides).length})
                    </button>
                </div>
            </div>

            {/* Category legend with running counts */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2 mb-4">
                {cats.map((c) => (
                    <div
                        key={c.key}
                        className={`rounded-lg border p-3 text-xs ${CAT_BADGE[c.key]?.cls || "bg-slate-50"}`}
                        data-testid={`osp-legend-${c.key}`}
                    >
                        <div className="font-extrabold text-sm mb-1">{c.label}</div>
                        <div className="opacity-80 mb-1">{c.desc}</div>
                        <div className="font-bold num" data-testid={`osp-legend-${c.key}-count`}>
                            {(counts.counts[c.key] || 0).toLocaleString("en-US")} طلب
                            <span className="opacity-60 mx-1">·</span>
                            {fmtMoney(counts.sums[c.key] || 0)} ر.س
                        </div>
                    </div>
                ))}
            </div>

            {loading ? (
                <div className="text-sm text-muted-foreground py-6 text-center">جاري التحميل…</div>
            ) : rows.length === 0 ? (
                <div className="text-sm text-muted-foreground py-6 text-center">لا توجد حالات طلبات في بياناتك بعد.</div>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full text-sm" data-testid="osp-table">
                        <thead className="text-xs text-muted-foreground bg-slate-50">
                            <tr>
                                <th className="text-right px-3 py-2 font-semibold">الحالة</th>
                                <th className="text-right px-3 py-2 font-semibold">الطلبات</th>
                                <th className="text-right px-3 py-2 font-semibold">الإجمالي</th>
                                <th className="text-right px-3 py-2 font-semibold w-[420px]">التصنيف</th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r) => {
                                const cur = effective(r);
                                const dirtyRow = overrides[r.status] !== undefined;
                                return (
                                    <tr
                                        key={r.status}
                                        className={`border-t border-border ${dirtyRow ? "bg-amber-50/60" : "hover:bg-accent/20"}`}
                                        data-testid={`osp-row-${r.status}`}
                                    >
                                        <td className="px-3 py-2 font-bold">
                                            {r.display}
                                            {r.is_overridden && !dirtyRow && (
                                                <span className="ms-1 text-[9px] px-1 py-0.5 rounded bg-indigo-100 text-indigo-700 font-bold">
                                                    معدَّل
                                                </span>
                                            )}
                                            {dirtyRow && (
                                                <span className="ms-1 text-[9px] px-1 py-0.5 rounded bg-amber-100 text-amber-800 font-bold">
                                                    غير محفوظ
                                                </span>
                                            )}
                                        </td>
                                        <td className="px-3 py-2 num">{r.orders_count.toLocaleString("en-US")}</td>
                                        <td className="px-3 py-2 num">{fmtMoney(r.total_amount)}</td>
                                        <td className="px-3 py-2">
                                            <div className="inline-flex flex-wrap gap-1" role="radiogroup" aria-label={`تصنيف ${r.display}`}>
                                                {cats.map((c) => {
                                                    const active = cur === c.key;
                                                    const meta = CAT_BADGE[c.key];
                                                    return (
                                                        <button
                                                            key={c.key}
                                                            type="button"
                                                            role="radio"
                                                            aria-checked={active}
                                                            onClick={() => setCategory(r.status, c.key)}
                                                            className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition-colors ${
                                                                active
                                                                    ? meta.cls + " ring-2 ring-offset-1 ring-current"
                                                                    : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50"
                                                            }`}
                                                            data-testid={`osp-cat-${r.status}-${c.key}`}
                                                        >
                                                            {c.label}
                                                        </button>
                                                    );
                                                })}
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            <p className="text-[11px] text-muted-foreground mt-4">
                ملاحظة: الطلبات «المعلّقة» تظهر في بطاقة منفصلة على لوحة التحكم والأصول، ولا تُحسب ضمن الصافي المؤكَّد.
            </p>
        </div>
    );
}
