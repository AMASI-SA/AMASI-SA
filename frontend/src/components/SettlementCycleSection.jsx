/**
 * Settlement Cycle Settings (Iter-90 phase A).
 *
 * Settings → دورة التسوية لكل بوابة دفع.
 * يربطها الـ Health endpoint ليصنّف كل مبلغ معلَّق إلى:
 * 🟢 in_cycle 🟡 awaiting 🟠 due_today 🔴 overdue.
 */
import { useEffect, useState } from "react";
import { Clock, Bell, ArrowsClockwise, CheckCircle, ArrowCounterClockwise } from "@phosphor-icons/react";
import api from "../lib/api";
import { toast } from "sonner";

const WEEKDAYS = ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"];

export default function SettlementCycleSection() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [draft, setDraft] = useState({}); // { gateway: {issuance_days, transfer_days, transfer_weekdays, alerts_enabled} }

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/settlement-cycle/settings");
            setData(data);
            const d = {};
            (data.gateways || []).forEach((g) => {
                d[g.key] = {
                    issuance_days: g.issuance_days,
                    transfer_days: g.transfer_days,
                    transfer_weekdays: [...(g.transfer_weekdays || [])],
                    alerts_enabled: g.alerts_enabled,
                };
            });
            setDraft(d);
        } catch {
            toast.error("تعذّر تحميل إعدادات دورة التسوية");
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, []);

    const setField = (gw, k, v) => setDraft((p) => ({ ...p, [gw]: { ...p[gw], [k]: v } }));
    const toggleDay = (gw, d) => setDraft((p) => {
        const cur = new Set(p[gw]?.transfer_weekdays || []);
        if (cur.has(d)) cur.delete(d); else cur.add(d);
        return { ...p, [gw]: { ...p[gw], transfer_weekdays: [...cur].sort() } };
    });

    const save = async () => {
        const items = Object.entries(draft).map(([gateway, v]) => ({ gateway, ...v }));
        setSaving(true);
        try {
            await api.put("/settlement-cycle/settings", { items });
            toast.success("تم حفظ إعدادات دورة التسوية");
            await load();
        } catch { toast.error("تعذّر الحفظ"); }
        finally { setSaving(false); }
    };

    const reset = async () => {
        if (!window.confirm("إعادة جميع البوابات للإعداد الافتراضي؟")) return;
        setSaving(true);
        try {
            await api.post("/settlement-cycle/reset");
            toast.success("تمت الإعادة للإفتراضي");
            await load();
        } catch { toast.error("تعذّر إعادة الضبط"); }
        finally { setSaving(false); }
    };

    if (loading) return <div className="rounded-xl border border-border bg-white p-6 animate-pulse h-48" data-testid="cycle-loading" />;

    return (
        <div className="rounded-xl border border-border bg-white p-6" data-testid="settlement-cycle-section">
            <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
                <div className="flex items-start gap-3">
                    <div className="w-10 h-10 rounded-lg bg-emerald-100 text-emerald-700 flex items-center justify-center">
                        <Clock size={22} weight="duotone" />
                    </div>
                    <div>
                        <h2 className="text-xl sm:text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>
                            دورة التسوية لكل بوابة
                        </h2>
                        <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
                            حدِّد لكل بوابة: مدة إصدار التسوية + مدة التحويل + أيام التحويل المسموحة. هذه القيم تُغذّي محرّك التنبيهات الذكي.
                        </p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <button type="button" onClick={reset} disabled={saving}
                        className="px-3 py-1.5 rounded-lg text-xs font-bold text-slate-600 bg-slate-100 hover:bg-slate-200 inline-flex items-center gap-1"
                        data-testid="cycle-reset-btn">
                        <ArrowCounterClockwise size={14} weight="bold" /> إعادة افتراضي
                    </button>
                    <button type="button" onClick={save} disabled={saving}
                        className="px-4 py-1.5 rounded-lg text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 inline-flex items-center gap-1 disabled:opacity-50"
                        data-testid="cycle-save-btn">
                        {saving ? <ArrowsClockwise size={14} className="animate-spin" /> : <CheckCircle size={14} weight="bold" />}
                        حفظ
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {(data?.gateways || []).map((g) => {
                    const d = draft[g.key] || {};
                    return (
                        <div key={g.key} className="rounded-lg border border-border bg-slate-50/50 p-4" data-testid={`cycle-card-${g.key}`}>
                            <div className="flex items-center justify-between mb-3">
                                <div className="font-extrabold text-base" style={{ fontFamily: "Tajawal" }}>{g.name_ar}</div>
                                <label className="inline-flex items-center gap-2 cursor-pointer">
                                    <input type="checkbox" checked={!!d.alerts_enabled}
                                        onChange={(e) => setField(g.key, "alerts_enabled", e.target.checked)}
                                        className="rounded" data-testid={`cycle-alerts-${g.key}`} />
                                    <span className="text-xs inline-flex items-center gap-1"><Bell size={12} /> تنبيهات</span>
                                </label>
                            </div>
                            <div className="grid grid-cols-2 gap-3 mb-3">
                                <div>
                                    <label className="block text-[11px] font-bold text-muted-foreground mb-1">إصدار التسوية (يوم)</label>
                                    <input type="number" min={0} max={90} value={d.issuance_days ?? ""}
                                        onChange={(e) => setField(g.key, "issuance_days", parseInt(e.target.value) || 0)}
                                        className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:ring-2 focus:ring-emerald-500 focus:outline-none num"
                                        data-testid={`cycle-issuance-${g.key}`} />
                                </div>
                                <div>
                                    <label className="block text-[11px] font-bold text-muted-foreground mb-1">التحويل البنكي (يوم)</label>
                                    <input type="number" min={0} max={30} value={d.transfer_days ?? ""}
                                        onChange={(e) => setField(g.key, "transfer_days", parseInt(e.target.value) || 0)}
                                        className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:ring-2 focus:ring-emerald-500 focus:outline-none num"
                                        data-testid={`cycle-transfer-${g.key}`} />
                                </div>
                            </div>
                            <div>
                                <label className="block text-[11px] font-bold text-muted-foreground mb-1">أيام التحويل المسموحة</label>
                                <div className="flex flex-wrap gap-1.5" data-testid={`cycle-weekdays-${g.key}`}>
                                    {WEEKDAYS.map((wd, idx) => {
                                        const active = (d.transfer_weekdays || []).includes(idx);
                                        return (
                                            <button key={idx} type="button" onClick={() => toggleDay(g.key, idx)}
                                                aria-pressed={active}
                                                className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition-colors ${active ? "bg-emerald-600 text-white border-emerald-700" : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50"}`}
                                                data-testid={`cycle-day-${g.key}-${idx}`}>
                                                {wd}
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
