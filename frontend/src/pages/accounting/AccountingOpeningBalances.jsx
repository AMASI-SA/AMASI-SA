import { useEffect, useMemo, useState } from "react";
import {
    CheckCircle,
    LockKey,
    Plus,
    ShieldCheck,
    Trash,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    activateAccountingP01,
    approveAccountingOpeningBalances,
    getAccountingOpeningBalances,
    previewAccountingOpeningBalances,
} from "../../services/accountingModule";
import { formatMoney, LoadingBlock } from "./AccountingShared";

const EVIDENCE = [
    ["banks_cash", "البنوك والصندوق"],
    ["providers", "سلة وطرق الدفع والتمويل"],
    ["couriers_cod", "شركات الشحن والتحصيل والموصلون"],
    ["inventory", "المخزون بالتكلفة"],
    ["suppliers", "الموردون والعملاء"],
    ["payroll_obligations", "الرواتب والسلف والعهد"],
    ["equity", "حقوق الملكية والضرائب والذمم الأخرى"],
];

const PROVIDER_LABELS = {
    salla: "سلة",
    tamara: "تمارا",
    tabby: "تابي",
    emkan: "إمكان",
};

const ACCOUNT_CATEGORIES = new Set(["bank", "cash"]);
const EMPLOYEE_CATEGORIES = new Set([
    "employee_advance",
    "employee_custody",
    "employee_salary_payable",
]);

function localCutoverDefault() {
    const now = new Date();
    const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
    }).formatToParts(now);
    const part = (type) => parts.find((item) => item.type === type)?.value || "";
    return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}

function errorText(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    if (detail?.code) {
        const extra = detail?.missing?.length ? ` — ${detail.missing.join("، ")}` : "";
        return detail.code + extra;
    }
    return fallback;
}

function newLine(category = "bank") {
    return {
        local_id: crypto.randomUUID(),
        category,
        entity_id: "",
        label: "",
        amount: "",
        direction: "normal",
    };
}

function MoneyTotals({ totals }) {
    if (!totals) return null;
    return (
        <div className="grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="text-xs font-bold text-slate-500">إجمالي المدين</div>
                <div className="mt-1 text-xl font-black text-slate-950">{formatMoney(totals.debit)}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="text-xs font-bold text-slate-500">إجمالي الدائن</div>
                <div className="mt-1 text-xl font-black text-slate-950">{formatMoney(totals.credit)}</div>
            </div>
            <div className={`rounded-xl border p-4 ${totals.balanced ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50"}`}>
                <div className="text-xs font-bold text-slate-500">الفرق</div>
                <div className={`mt-1 text-xl font-black ${totals.balanced ? "text-emerald-800" : "text-rose-800"}`}>
                    {formatMoney(totals.difference)}
                </div>
            </div>
        </div>
    );
}

function EntityInput({ line, data, onChange }) {
    if (ACCOUNT_CATEGORIES.has(line.category)) {
        const wanted = line.category === "bank" ? "bank" : "cash";
        const accounts = (data?.accounts || []).filter((item) => item.account_type === wanted);
        return (
            <select
                value={line.entity_id}
                onChange={(event) => onChange({ entity_id: event.target.value })}
                className="min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs"
                aria-label="الحساب"
            >
                <option value="">اختر الحساب</option>
                {accounts.map((item) => <option key={item.id} value={item.id}>{item.name || item.id}</option>)}
            </select>
        );
    }
    if (line.category === "provider_receivable") {
        return (
            <select
                value={line.entity_id}
                onChange={(event) => onChange({ entity_id: event.target.value })}
                className="min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs"
                aria-label="مزود الدفع"
            >
                <option value="">اختر المزود</option>
                {(data?.providers || []).map((provider) => (
                    <option key={provider} value={provider}>{PROVIDER_LABELS[provider] || provider}</option>
                ))}
            </select>
        );
    }
    if (EMPLOYEE_CATEGORIES.has(line.category)) {
        return (
            <select
                value={line.entity_id}
                onChange={(event) => onChange({ entity_id: event.target.value })}
                className="min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs"
                aria-label="الموظف"
            >
                <option value="">اختر الموظف</option>
                {(data?.employees || []).map((item) => (
                    <option key={item.id} value={item.id}>{item.name || item.id}</option>
                ))}
            </select>
        );
    }
    return (
        <input
            value={line.entity_id}
            onChange={(event) => onChange({ entity_id: event.target.value })}
            className="min-h-10 w-full rounded-lg border border-slate-200 px-2 text-xs"
            placeholder="المعرّف / الحساب"
            aria-label="معرف الرصيد"
        />
    );
}

export default function AccountingOpeningBalances() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");
    const [cutoverAt, setCutoverAt] = useState(localCutoverDefault);
    const [evidenceSheet, setEvidenceSheet] = useState("");
    const [evidence, setEvidence] = useState(() => Object.fromEntries(EVIDENCE.map(([key]) => [key, ""])));
    const [notes, setNotes] = useState("");
    const [lines, setLines] = useState([newLine("bank")]);
    const [preview, setPreview] = useState(null);
    const [approveChecked, setApproveChecked] = useState(false);
    const [activationRef, setActivationRef] = useState("");
    const [activateChecked, setActivateChecked] = useState(false);

    async function refresh() {
        setLoading(true);
        try {
            const next = await getAccountingOpeningBalances();
            setData(next);
            if (next?.preview) setPreview(next.preview);
            const state = next?.cutover || {};
            if (state.cutover_at) {
                const d = new Date(state.cutover_at);
                const parts = new Intl.DateTimeFormat("en-CA", {
                    timeZone: "Asia/Riyadh",
                    year: "numeric", month: "2-digit", day: "2-digit",
                    hour: "2-digit", minute: "2-digit", hour12: false,
                }).formatToParts(d);
                const part = (type) => parts.find((item) => item.type === type)?.value || "";
                setCutoverAt(`${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`);
            }
            if (state.evidence_sheet_ref) setEvidenceSheet(state.evidence_sheet_ref);
            if (state.evidence_sections) {
                setEvidence((old) => ({
                    ...old,
                    ...Object.fromEntries(EVIDENCE.map(([key]) => [
                        key,
                        state.evidence_sections?.[key]?.ref || state.evidence_sections?.[key] || old[key] || "",
                    ])),
                }));
            }
        } catch (error) {
            toast.error(errorText(error, "تعذر تحميل الأرصدة الافتتاحية"));
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { refresh(); }, []);

    const categories = data?.categories || [];
    const committed = Boolean(data?.cutover?.opening_balance_txn_group_id);
    const active = data?.cutover?.status === "active";

    const normalizedLines = useMemo(() => lines.map(({ local_id, ...line }) => ({
        ...line,
        amount: line.amount || "0",
    })), [lines]);

    function patchLine(localId, patch) {
        setLines((current) => current.map((line) => {
            if (line.local_id !== localId) return line;
            const next = { ...line, ...patch };
            if (patch.category) {
                next.entity_id = "";
                next.label = "";
            }
            return next;
        }));
    }

    async function createPreview(event) {
        event.preventDefault();
        if (!cutoverAt) return toast.error("حدد تاريخ ووقت القطع");
        if (!evidenceSheet.trim()) return toast.error("أدخل مرجع ورقة الأدلة");
        if (Object.values(evidence).some((value) => !String(value).trim())) {
            return toast.error("أكمل مراجع الأدلة السبعة، ويمكن توثيق الرصيد صفر بمرجع صريح");
        }
        if (normalizedLines.some((line) => !line.entity_id || Number(line.amount) <= 0)) {
            return toast.error("أكمل الحساب والمبلغ لكل رصيد افتتاحي");
        }
        setBusy("preview");
        try {
            const result = await previewAccountingOpeningBalances({
                cutover_at: cutoverAt + ":00+03:00",
                evidence_sheet_ref: evidenceSheet.trim(),
                evidence_sections: evidence,
                lines: normalizedLines,
                notes: notes.trim(),
            });
            setPreview(result);
            setApproveChecked(false);
            toast.success("تم إنشاء معاينة متوازنة دون أي قيد مالي");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر إنشاء المعاينة"));
        } finally {
            setBusy("");
        }
    }

    async function approve() {
        if (!preview?.id || !approveChecked) return;
        setBusy("approve");
        try {
            const result = await approveAccountingOpeningBalances(preview.id);
            toast.success(result.state === "already_posted" ? "القيد الافتتاحي مرحّل مسبقًا" : "تم اعتماد وترحيل القيد الافتتاحي مرة واحدة");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر اعتماد القيد الافتتاحي"));
        } finally {
            setBusy("");
        }
    }

    async function activate() {
        if (!activateChecked || activationRef.trim().length < 3) return;
        setBusy("activate");
        try {
            await activateAccountingP01(activationRef.trim());
            toast.success("تم تفعيل P01 لهذه البيئة. P02 يبقى مقفلاً.");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر تفعيل P01"));
        } finally {
            setBusy("");
        }
    }

    if (loading && !data) return <LoadingBlock label="جاري تحميل الأرصدة الافتتاحية…" />;

    return (
        <div className="space-y-5" dir="rtl" data-testid="accounting-opening-balances">
            <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
                <div className="flex items-start gap-3">
                    <ShieldCheck size={28} weight="duotone" className="shrink-0 text-emerald-800" />
                    <div>
                        <h2 className="text-xl font-black text-emerald-950">رصيد افتتاحي جديد لميزان 2</h2>
                        <p className="mt-2 text-sm font-semibold leading-6 text-emerald-900">
                            أدخل الأرصدة التي تثبتها مستندات يوم القطع فقط. لا يقرأ النظام أرصدة ميزان القديم ولا يرحّل قيوده. حقوق الملكية تُحسب تلقائيًا لموازنة القيد.
                        </p>
                    </div>
                </div>
            </section>

            {active && (
                <div className="flex items-center gap-3 rounded-2xl border border-emerald-300 bg-white p-5 text-emerald-900">
                    <CheckCircle size={26} weight="fill" />
                    <div>
                        <div className="font-black">P01 نشط والقيد الافتتاحي موثق</div>
                        <div className="mt-1 text-xs font-semibold">مجموعة القيد: <span dir="ltr">{data?.cutover?.opening_balance_txn_group_id}</span></div>
                    </div>
                </div>
            )}

            {!committed && (
                <form onSubmit={createPreview} className="space-y-5">
                    <section className="rounded-2xl border border-slate-200 bg-white p-5">
                        <h3 className="font-black text-slate-950">1. يوم القطع والأدلة</h3>
                        <div className="mt-4 grid gap-4 md:grid-cols-2">
                            <label className="text-xs font-extrabold text-slate-700">
                                تاريخ ووقت القطع — Asia/Riyadh
                                <input type="datetime-local" value={cutoverAt} onChange={(event) => setCutoverAt(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 px-3" />
                            </label>
                            <label className="text-xs font-extrabold text-slate-700">
                                مرجع ورقة الأدلة الموقعة
                                <input value={evidenceSheet} onChange={(event) => setEvidenceSheet(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 px-3" placeholder="مثال: UAT-OPENING-20260921" />
                            </label>
                        </div>
                        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                            {EVIDENCE.map(([key, label]) => (
                                <label key={key} className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs font-extrabold text-slate-700">
                                    {label}
                                    <input value={evidence[key]} onChange={(event) => setEvidence((old) => ({ ...old, [key]: event.target.value }))} className="mt-2 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2" placeholder="مرجع مستند أو إثبات صفر" />
                                </label>
                            ))}
                        </div>
                    </section>

                    <section className="rounded-2xl border border-slate-200 bg-white p-5">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <div>
                                <h3 className="font-black text-slate-950">2. الأرصدة الافتتاحية</h3>
                                <p className="mt-1 text-xs font-semibold text-slate-500">أدخل الرصيد الطبيعي فقط؛ ميزان يحدد المدين والدائن حسب نوع الرصيد.</p>
                            </div>
                            <button type="button" onClick={() => setLines((old) => [...old, newLine()])} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 px-3 text-xs font-extrabold">
                                <Plus size={17} /> إضافة رصيد
                            </button>
                        </div>
                        <div className="mt-4 space-y-3">
                            {lines.map((line) => (
                                <div key={line.local_id} className="grid gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 lg:grid-cols-[1.2fr_1.2fr_1fr_.8fr_.75fr_auto] lg:items-end">
                                    <label className="text-[11px] font-extrabold text-slate-600">
                                        النوع
                                        <select value={line.category} onChange={(event) => patchLine(line.local_id, { category: event.target.value })} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs">
                                            {categories.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                                        </select>
                                    </label>
                                    <label className="text-[11px] font-extrabold text-slate-600">
                                        الحساب / الجهة
                                        <div className="mt-1"><EntityInput line={line} data={data} onChange={(patch) => patchLine(line.local_id, patch)} /></div>
                                    </label>
                                    <label className="text-[11px] font-extrabold text-slate-600">
                                        الاسم الظاهر
                                        <input value={line.label} onChange={(event) => patchLine(line.local_id, { label: event.target.value })} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs" placeholder="اختياري" />
                                    </label>
                                    <label className="text-[11px] font-extrabold text-slate-600">
                                        المبلغ
                                        <input type="number" min="0.01" step="0.01" value={line.amount} onChange={(event) => patchLine(line.local_id, { amount: event.target.value })} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-left font-mono text-xs" dir="ltr" />
                                    </label>
                                    <label className="text-[11px] font-extrabold text-slate-600">
                                        الاتجاه
                                        <select value={line.direction} onChange={(event) => patchLine(line.local_id, { direction: event.target.value })} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs">
                                            <option value="normal">طبيعي</option>
                                            <option value="opposite">عكسي</option>
                                        </select>
                                    </label>
                                    <button type="button" onClick={() => setLines((old) => old.filter((item) => item.local_id !== line.local_id))} disabled={lines.length === 1} className="min-h-10 rounded-lg border border-rose-200 bg-white px-3 text-rose-700 disabled:opacity-30" aria-label="حذف الرصيد">
                                        <Trash size={17} />
                                    </button>
                                </div>
                            ))}
                        </div>
                        <label className="mt-4 block text-xs font-extrabold text-slate-700">
                            ملاحظات
                            <textarea value={notes} onChange={(event) => setNotes(event.target.value)} className="mt-1 min-h-20 w-full rounded-xl border border-slate-200 p-3 text-sm" />
                        </label>
                        <div className="mt-4 flex justify-end">
                            <button disabled={busy === "preview"} className="min-h-11 rounded-xl bg-slate-900 px-5 text-sm font-black text-white disabled:opacity-40">
                                {busy === "preview" ? "جاري التحقق…" : "إنشاء معاينة بدون ترحيل"}
                            </button>
                        </div>
                    </section>
                </form>
            )}

            {preview && (
                <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5" data-testid="opening-preview">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <h3 className="font-black text-slate-950">3. معاينة القيد الافتتاحي</h3>
                            <p className="mt-1 text-xs font-semibold text-slate-500">المعاينة لا تغيّر أي رصيد. الترحيل يحدث مرة واحدة فقط بعد الاعتماد الصريح.</p>
                        </div>
                        <span className={`rounded-full px-3 py-1 text-xs font-extrabold ${preview.status === "posted" ? "bg-emerald-100 text-emerald-800" : "bg-sky-100 text-sky-800"}`}>
                            {preview.status === "posted" ? "مرحّل" : "معاينة"}
                        </span>
                    </div>
                    <MoneyTotals totals={preview.totals} />
                    <div className="overflow-x-auto rounded-xl border border-slate-200">
                        <table className="min-w-full text-xs">
                            <thead className="bg-slate-50">
                                <tr>
                                    <th className="p-3 text-right">الحساب</th>
                                    <th className="p-3 text-right">مدين</th>
                                    <th className="p-3 text-right">دائن</th>
                                    <th className="p-3 text-right">الدليل</th>
                                </tr>
                            </thead>
                            <tbody>
                                {(preview.lines || []).map((row, index) => (
                                    <tr key={row.category + row.entity_id + index} className="border-t border-slate-100">
                                        <td className="p-3 font-bold">{row.label} <span className="text-[10px] text-slate-400" dir="ltr">{row.entity_id}</span></td>
                                        <td className="p-3 font-mono">{row.side === "debit" ? formatMoney(row.amount) : "—"}</td>
                                        <td className="p-3 font-mono">{row.side === "credit" ? formatMoney(row.amount) : "—"}</td>
                                        <td className="p-3 text-slate-500">{row.evidence_ref}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    {preview.status === "previewed" && !committed && (
                        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                            <label className="flex items-start gap-2 text-xs font-extrabold leading-6 text-amber-950">
                                <input type="checkbox" checked={approveChecked} onChange={(event) => setApproveChecked(event.target.checked)} className="mt-1" />
                                راجعت تاريخ القطع والأرصدة والأدلة، وأوافق على إنشاء القيد الافتتاحي مرة واحدة. لا توجد قيود قديمة ستُرحّل.
                            </label>
                            <button type="button" onClick={approve} disabled={!approveChecked || busy === "approve"} className="mt-3 min-h-11 rounded-xl bg-amber-800 px-5 text-sm font-black text-white disabled:opacity-40">
                                {busy === "approve" ? "جاري الترحيل الذري…" : "اعتماد وترحيل القيد الافتتاحي"}
                            </button>
                        </div>
                    )}
                </section>
            )}

            {committed && !active && (
                <section className="rounded-2xl border border-violet-200 bg-violet-50 p-5" data-testid="opening-activation">
                    <div className="flex items-start gap-3">
                        <LockKey size={24} className="shrink-0 text-violet-800" />
                        <div className="flex-1">
                            <h3 className="font-black text-violet-950">4. تفعيل P01 بعد القيد الافتتاحي</h3>
                            <p className="mt-1 text-xs font-semibold leading-6 text-violet-800">
                                التفعيل منفصل عن اعتماد الرصيد. لن يفتح P02 للشحن/COD.
                            </p>
                            <input value={activationRef} onChange={(event) => setActivationRef(event.target.value)} className="mt-3 min-h-10 w-full max-w-xl rounded-lg border border-violet-200 bg-white px-3 text-xs" placeholder="مرجع قرار التفعيل / UAT run" />
                            <label className="mt-3 flex items-start gap-2 text-xs font-extrabold text-violet-950">
                                <input type="checkbox" checked={activateChecked} onChange={(event) => setActivateChecked(event.target.checked)} className="mt-1" />
                                أؤكد تفعيل P01 لهذه البيئة بعد اكتمال الأدلة والقيد الافتتاحي.
                            </label>
                            <button type="button" onClick={activate} disabled={!activateChecked || activationRef.trim().length < 3 || busy === "activate"} className="mt-3 min-h-11 rounded-xl bg-violet-800 px-5 text-sm font-black text-white disabled:opacity-40">
                                {busy === "activate" ? "جاري التحقق…" : "تفعيل P01"}
                            </button>
                        </div>
                    </div>
                </section>
            )}

            {data?.opening_posted_verified && (
                <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs font-extrabold text-emerald-900">
                    <CheckCircle size={19} weight="fill" /> مجموعة القيد الافتتاحي متوازنة ومتحقق منها.
                </div>
            )}
            {data?.cutover?.p02_shipping_cod_enabled === true && (
                <div className="flex items-center gap-2 rounded-xl border border-rose-300 bg-rose-50 p-3 text-xs font-extrabold text-rose-900">
                    <WarningCircle size={19} weight="fill" /> P02 غير مقفل؛ أوقف هذا المسار ولا تعتمد الرصيد.
                </div>
            )}
        </div>
    );
}
