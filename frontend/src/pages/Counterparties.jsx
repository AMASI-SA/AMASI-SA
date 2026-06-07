/**
 * Counterparties Management — Iter-99 Phase 2
 *
 * A single, dedicated screen for managing the unified registry of:
 *   • Suppliers           (مورد / جهة عامة)
 *   • Ad Accounts         (Snapchat 1, Snapchat 2, TikTok …)
 *   • General             (any other recurring counterparty)
 *
 * The fuzzy match is a WARNING only — never an auto-merge.
 * Multiple "Snapchat Account 1 / 2" stay distinct unless the user
 * explicitly chooses to use an existing match.
 */
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
    UsersThree, Plus, PencilSimple, Trash, MagnifyingGlass,
} from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";


const KIND_LABEL = {
    supplier:  "مورد / جهة عامة",
    ad_account: "حساب إعلاني",
    general:   "طرف عام",
};

const AD_PROVIDER_LABEL = {
    snapchat: "Snapchat",
    tiktok:   "TikTok",
    meta:     "Meta",
};

const inputCls =
    "w-full px-3 py-2.5 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-violet-500";


function CreateForm({ onCreated }) {
    const [kind, setKind] = useState("supplier");
    const [adProvider, setAdProvider] = useState("snapchat");
    const [name, setName] = useState("");
    const [notes, setNotes] = useState("");
    const [busy, setBusy] = useState(false);
    const [warning, setWarning] = useState(null);   // { suggestion }

    const reset = () => {
        setName(""); setNotes(""); setWarning(null);
    };

    const submit = async (force = false) => {
        if (!name.trim()) { toast.error("الاسم مطلوب"); return; }
        setBusy(true);
        try {
            const body = { kind, name: name.trim(), notes: notes.trim(), force };
            if (kind === "ad_account") body.ad_provider = adProvider;
            const { data } = await api.post("/counterparties", body);
            toast.success(`تمت إضافة "${data.name}"`);
            reset();
            onCreated();
        } catch (e) {
            const d = e.response?.data?.detail;
            if (typeof d === "object" && d?.message === "similar_name_exists") {
                setWarning({ suggestion: d.suggestion });
                toast.warning(`اسم مشابه موجود: ${d.suggestion?.name}`);
            } else if (typeof d === "object" && d?.message === "duplicate") {
                toast.error(`الاسم موجود مسبقاً بنفس الشكل: ${d.existing?.name}`);
            } else {
                toast.error(formatApiErrorDetail(d) || "تعذّر الإضافة");
            }
        } finally { setBusy(false); }
    };

    return (
        <form
            onSubmit={(e) => { e.preventDefault(); submit(false); }}
            className="bg-white border border-slate-200 rounded-xl p-5 space-y-3"
            data-testid="cp-create-form"
        >
            <div className="flex items-center gap-2 pb-3 border-b border-slate-100">
                <Plus size={20} weight="duotone" className="text-violet-700" />
                <h2 className="font-bold text-base text-slate-900">إضافة طرف جديد</h2>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">النوع *</label>
                    <select value={kind} onChange={(e) => setKind(e.target.value)} className={inputCls} data-testid="cp-create-kind">
                        <option value="supplier">{KIND_LABEL.supplier}</option>
                        <option value="ad_account">{KIND_LABEL.ad_account}</option>
                        <option value="general">{KIND_LABEL.general}</option>
                    </select>
                </div>
                {kind === "ad_account" && (
                    <div>
                        <label className="block text-xs font-bold text-slate-700 mb-1.5">المنصة *</label>
                        <select value={adProvider} onChange={(e) => setAdProvider(e.target.value)} className={inputCls} data-testid="cp-create-provider">
                            {Object.entries(AD_PROVIDER_LABEL).map(([k, v]) => (
                                <option key={k} value={k}>{v}</option>
                            ))}
                        </select>
                    </div>
                )}
                <div className={kind === "ad_account" ? "" : "sm:col-span-2"}>
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">الاسم *</label>
                    <input
                        value={name}
                        onChange={(e) => { setName(e.target.value); setWarning(null); }}
                        className={inputCls}
                        placeholder={kind === "ad_account" ? "مثال: Snapchat Account 1" : "اسم المورد/الجهة"}
                        data-testid="cp-create-name"
                    />
                </div>
                <div className="sm:col-span-2">
                    <label className="block text-xs font-bold text-slate-700 mb-1.5">ملاحظات</label>
                    <input value={notes} onChange={(e) => setNotes(e.target.value)} className={inputCls} data-testid="cp-create-notes" />
                </div>
            </div>

            {warning && (
                <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-xs space-y-2" data-testid="cp-create-warning">
                    <div>
                        ⚠️ قد يكون هذا الطرف موجوداً مسبقاً باسم:
                        <b className="mx-1">{warning.suggestion?.name}</b>
                        {warning.suggestion?.ad_provider && (
                            <span className="text-slate-500"> ({AD_PROVIDER_LABEL[warning.suggestion.ad_provider]})</span>
                        )}
                    </div>
                    <div className="text-slate-600">
                        إذا كان حساباً منفصلاً (مثل Snapchat 1 و Snapchat 2) اضغط <b>&quot;أنشئ منفصلاً&quot;</b>.
                        التطابق التقريبي تنبيه فقط، ولا يتم الدمج تلقائياً.
                    </div>
                    <div className="flex gap-2 flex-wrap pt-1">
                        <button
                            type="button"
                            onClick={() => submit(true)}
                            disabled={busy}
                            className="px-3 py-1.5 rounded-lg bg-rose-700 text-white text-xs font-bold disabled:opacity-50"
                            data-testid="cp-create-force-btn"
                        >
                            أنشئ منفصلاً رغم التشابه
                        </button>
                        <button
                            type="button"
                            onClick={() => { setWarning(null); reset(); }}
                            className="px-3 py-1.5 rounded-lg bg-white border border-slate-300 text-slate-700 text-xs font-bold"
                        >
                            إلغاء
                        </button>
                    </div>
                </div>
            )}

            <div className="flex justify-end pt-1">
                <button
                    type="submit"
                    disabled={busy}
                    className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 disabled:opacity-50"
                    data-testid="cp-create-submit"
                >
                    {busy ? "جاري الحفظ…" : "إضافة"}
                </button>
            </div>
        </form>
    );
}


function RowActions({ row, onSaved }) {
    const [editing, setEditing] = useState(false);
    const [name, setName] = useState(row.name);
    const [notes, setNotes] = useState(row.notes || "");
    const [busy, setBusy] = useState(false);

    const save = async () => {
        if (!name.trim()) { toast.error("الاسم مطلوب"); return; }
        setBusy(true);
        try {
            await api.put(`/counterparties/${row.id}`, { name: name.trim(), notes: notes.trim() });
            toast.success("تم التعديل");
            setEditing(false);
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };

    const remove = async () => {
        if (!window.confirm(`حذف "${row.name}"؟`)) return;
        setBusy(true);
        try {
            await api.delete(`/counterparties/${row.id}`);
            toast.success("تم الحذف");
            onSaved();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail));
        } finally { setBusy(false); }
    };

    if (editing) {
        return (
            <div className="flex flex-col sm:flex-row gap-2">
                <input value={name} onChange={(e) => setName(e.target.value)} className={`${inputCls} text-xs`} data-testid={`cp-edit-name-${row.id}`} />
                <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="ملاحظات" className={`${inputCls} text-xs`} />
                <div className="flex gap-1 shrink-0">
                    <button onClick={save} disabled={busy} className="px-3 py-1.5 rounded bg-emerald-700 text-white text-xs font-bold disabled:opacity-50" data-testid={`cp-edit-save-${row.id}`}>حفظ</button>
                    <button onClick={() => setEditing(false)} className="px-3 py-1.5 rounded bg-slate-200 text-slate-700 text-xs font-bold">إلغاء</button>
                </div>
            </div>
        );
    }

    return (
        <div className="flex gap-1.5">
            <button
                onClick={() => setEditing(true)}
                className="p-1.5 rounded hover:bg-slate-100 text-slate-600 hover:text-violet-700"
                title="تعديل"
                data-testid={`cp-edit-btn-${row.id}`}
            >
                <PencilSimple size={16} />
            </button>
            <button
                onClick={remove}
                disabled={busy}
                className="p-1.5 rounded hover:bg-rose-50 text-slate-600 hover:text-rose-700 disabled:opacity-50"
                title="حذف"
                data-testid={`cp-delete-btn-${row.id}`}
            >
                <Trash size={16} />
            </button>
        </div>
    );
}


export default function Counterparties() {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [filterKind, setFilterKind] = useState("");
    const [search, setSearch] = useState("");

    const load = async () => {
        setLoading(true);
        try {
            const { data } = await api.get("/counterparties");
            setItems(data?.items || []);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "تعذّر التحميل");
        } finally { setLoading(false); }
    };

    useEffect(() => { load(); }, []);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        return items.filter((x) => {
            if (filterKind && x.kind !== filterKind) return false;
            if (q && !x.name.toLowerCase().includes(q) && !(x.notes || "").toLowerCase().includes(q)) return false;
            return true;
        });
    }, [items, filterKind, search]);

    const counts = useMemo(() => ({
        all:        items.length,
        supplier:   items.filter((i) => i.kind === "supplier").length,
        ad_account: items.filter((i) => i.kind === "ad_account").length,
        general:    items.filter((i) => i.kind === "general").length,
    }), [items]);

    return (
        <div dir="rtl" data-testid="counterparties-page" className="space-y-5">
            <div>
                <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-2">
                    <UsersThree size={28} weight="duotone" className="text-violet-700" />
                    قائمة الأطراف الموحَّدة
                </h1>
                <p className="text-sm text-slate-500 mt-1">
                    مرجع وحيد لأسماء المورّدين والحسابات الإعلانية. كل التزام جديد يربط بـ <b>طرف</b> من هنا — لا تكتب الاسم يدوياً.
                </p>
            </div>

            <CreateForm onCreated={load} />

            <div className="bg-white border border-slate-200 rounded-xl">
                <div className="p-4 border-b border-slate-100 flex flex-col sm:flex-row sm:items-center gap-3">
                    <div className="flex gap-2 flex-wrap">
                        {[
                            { v: "",           label: `الكل (${counts.all})` },
                            { v: "supplier",   label: `${KIND_LABEL.supplier} (${counts.supplier})` },
                            { v: "ad_account", label: `${KIND_LABEL.ad_account} (${counts.ad_account})` },
                            { v: "general",    label: `${KIND_LABEL.general} (${counts.general})` },
                        ].map((b) => (
                            <button
                                key={b.v || "all"}
                                onClick={() => setFilterKind(b.v)}
                                data-testid={`cp-filter-${b.v || "all"}`}
                                className={`px-3 py-1.5 rounded-lg text-xs font-bold transition ${
                                    filterKind === b.v
                                        ? "bg-slate-900 text-white"
                                        : "bg-white border border-slate-200 text-slate-700 hover:bg-slate-50"
                                }`}
                            >
                                {b.label}
                            </button>
                        ))}
                    </div>
                    <div className="sm:mr-auto relative">
                        <MagnifyingGlass size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            placeholder="بحث في الأسماء أو الملاحظات…"
                            className={`${inputCls} pr-8 sm:w-72`}
                            data-testid="cp-search"
                        />
                    </div>
                </div>

                {loading ? (
                    <div className="p-10 text-center text-slate-500 text-sm">جاري التحميل…</div>
                ) : filtered.length === 0 ? (
                    <div className="p-10 text-center text-slate-500 text-sm" data-testid="cp-empty">
                        لا توجد أطراف مسجَّلة بعد. ابدأ بإضافة طرف من النموذج أعلاه.
                    </div>
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead className="bg-slate-50 text-slate-600 text-xs">
                                <tr>
                                    <th className="text-right font-bold p-3">الاسم</th>
                                    <th className="text-right font-bold p-3">النوع</th>
                                    <th className="text-right font-bold p-3">ملاحظات</th>
                                    <th className="text-right font-bold p-3 w-32">إجراءات</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filtered.map((row) => (
                                    <tr key={row.id} className="border-t border-slate-100 hover:bg-slate-50/60" data-testid={`cp-row-${row.id}`}>
                                        <td className="p-3 font-bold text-slate-900">{row.name}</td>
                                        <td className="p-3">
                                            <span className="inline-block px-2 py-0.5 rounded text-[11px] font-bold bg-violet-50 text-violet-800">
                                                {KIND_LABEL[row.kind] || row.kind}
                                            </span>
                                            {row.kind === "ad_account" && row.ad_provider && (
                                                <span className="inline-block mr-1 px-2 py-0.5 rounded text-[11px] font-bold bg-slate-100 text-slate-700">
                                                    {AD_PROVIDER_LABEL[row.ad_provider] || row.ad_provider}
                                                </span>
                                            )}
                                        </td>
                                        <td className="p-3 text-slate-600 text-xs">{row.notes || "—"}</td>
                                        <td className="p-3"><RowActions row={row} onSaved={load} /></td>
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
