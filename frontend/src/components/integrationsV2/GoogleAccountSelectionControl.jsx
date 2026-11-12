import { useEffect, useId, useRef, useState } from "react";
import { CaretDown, FloppyDisk, SpinnerGap } from "@phosphor-icons/react";
import { getGoogleAccountSelection, saveGoogleAccountSelection } from "../../services/googleIntegrationsV2";

const savedIds = (selection) => new Set((selection?.accounts || [])
    .filter((account) => account.selected).map((account) => account.account_id));

export default function GoogleAccountSelectionControl() {
    const panelId = useId();
    const [open, setOpen] = useState(false);
    const [selection, setSelection] = useState(null);
    const [selectedIds, setSelectedIds] = useState(new Set());
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");
    const [needsReload, setNeedsReload] = useState(false);
    const [saved, setSaved] = useState(false);
    const requested = useRef(false);
    const sequence = useRef(0);
    useEffect(() => () => { sequence.current += 1; }, []);

    async function loadSelection() {
        const version = ++sequence.current;
        requested.current = true;
        setLoading(true);
        setError("");
        setSaved(false);
        try {
            const value = await getGoogleAccountSelection();
            if (version !== sequence.current) return;
            setSelection(value);
            setSelectedIds(savedIds(value));
            setNeedsReload(false);
        } catch (reason) {
            if (version !== sequence.current) return;
            setError(reason?.response?.status === 403
                ? "اختيار حسابات Google للتقارير متاح لمالك المتجر."
                : "تعذر تحميل حسابات Google المحفوظة. أعد المحاولة.");
            setNeedsReload(true);
        } finally {
            if (version === sequence.current) setLoading(false);
        }
    }

    function togglePanel() {
        if (!open && !requested.current) void loadSelection();
        setOpen((value) => !value);
    }

    function toggleAccount(id) {
        setSaved(false);
        setSelectedIds((current) => {
            const next = new Set(current);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }

    const accounts = selection?.accounts || [];
    const persisted = savedIds(selection);
    const changed = Boolean(selection) && (!selection.selection_configured || selection.selection_required
        || persisted.size !== selectedIds.size || [...selectedIds].some((id) => !persisted.has(id)));
    const busy = loading || saving;
    const canSave = changed && selectedIds.size > 0 && !busy && !needsReload;

    async function persistSelection() {
        if (!canSave) return;
        const version = ++sequence.current;
        setSaving(true);
        setError("");
        setSaved(false);
        try {
            const value = await saveGoogleAccountSelection([...selectedIds], selection.expected_account_ids);
            if (version !== sequence.current) return;
            setSelection(value);
            setSelectedIds(savedIds(value));
            setSaved(true);
        } catch (reason) {
            if (version !== sequence.current) return;
            const status = reason?.response?.status;
            const outcomeUnknown = reason?.applyOutcomeUnknown === true
                || reason?.response?.data?.detail?.apply_outcome_unknown === true;
            if (status === 409 || outcomeUnknown) {
                setNeedsReload(true);
                setError(outcomeUnknown
                    ? "لم نتأكد من نتيجة الحفظ. أعد تحميل الحسابات للتحقق من الاختيار المحفوظ قبل المحاولة مجددًا."
                    : "تغيّرت قائمة حسابات Google. أعد تحميل الحسابات وراجع الاختيار قبل الحفظ.");
            } else {
                setError(status === 403 ? "اختيار حسابات Google للتقارير متاح لمالك المتجر."
                    : "تعذر حفظ حسابات Google المحددة. أعد المحاولة.");
            }
        } finally {
            if (version === sequence.current) setSaving(false);
        }
    }

    return <section className="rounded-xl border border-slate-200 bg-slate-50" data-testid="google-account-selection-control">
        <button type="button" onClick={togglePanel} aria-expanded={open} aria-controls={panelId}
            className="flex min-h-11 w-full items-center justify-between gap-3 px-4 py-3 text-right text-xs font-extrabold text-slate-800"
            data-testid="google-account-selection-toggle">
            <span>إدارة الحسابات والتقارير · Google</span>
            <CaretDown size={16} className={open ? "rotate-180" : ""} />
        </button>
        {open && <div id={panelId} className="border-t border-slate-200 bg-white p-4">
            <h3 className="text-sm font-extrabold text-slate-800">حسابات Google المعتمدة في التقارير</h3>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
                اختر الحسابات الفعالة التي يدخل صرفها في التقارير. تبقى الحسابات الأخرى وبياناتها محفوظة عند استبعادها من هذا النطاق.
            </p>
            {loading && <p role="status" className="mt-3 flex items-center gap-2 text-xs text-slate-500">
                <SpinnerGap size={16} className="animate-spin" /> جاري تحميل الحسابات…
            </p>}
            {error && <p role="alert" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">{error}</p>}
            {!loading && selection && <>
                <p className="mt-3 text-xs font-bold text-slate-600" data-testid="google-account-selection-count">{selectedIds.size} من {accounts.length} حساب محدد</p>
                {selection.missing_selected_account_ids?.length > 0 && <p className="mt-2 text-xs leading-5 text-amber-800">
                    بعض الحسابات المحددة سابقًا غير متاحة في القائمة الحالية. راجع الحسابات الظاهرة واحفظ النطاق لتأكيد اعتمادها في التقارير.
                </p>}
                {accounts.length ? <div className="mt-2 space-y-2">
                    {accounts.map((account) => <label key={account.account_id} className="flex items-start justify-between gap-3 rounded-lg border border-slate-100 px-3 py-2 text-xs">
                        <span className="flex min-w-0 items-start gap-2">
                            <input type="checkbox" checked={selectedIds.has(account.account_id)} onChange={() => toggleAccount(account.account_id)}
                                disabled={busy || needsReload} className="mt-1" aria-label={`اختيار ${account.display_name || account.account_id}`} />
                            <span className="min-w-0"><span className="block truncate font-bold text-slate-700">{account.display_name || "حساب Google"}</span>
                                <span className="block font-mono text-[10px] text-slate-400" dir="ltr">{account.account_id}</span></span>
                        </span>
                        <span className="shrink-0 text-left font-mono text-[10px] text-slate-500">{account.currency || "—"}<br />{account.timezone || "—"}</span>
                    </label>)}
                </div> : <p className="mt-3 text-xs leading-5 text-slate-500">لا توجد حسابات Google محفوظة لهذا المتجر. أكمل ربط Google أولًا.</p>}
                {accounts.length > 0 && selectedIds.size === 0 && <p className="mt-3 text-xs leading-5 text-amber-800">
                    {selection.selection_configured ? "لا يوجد حساب متاح محدد للتقارير. " : "لم يُحفظ اختيار حسابات التقارير بعد. "}اختر حسابًا واحدًا على الأقل ثم احفظ الاختيار.
                </p>}
                {accounts.length > 0 && <button type="button" onClick={persistSelection} disabled={!canSave}
                    className="mt-3 inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-blue-700 bg-blue-700 px-3 text-xs font-extrabold text-white disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid="google-save-account-selection">
                    {saving ? <SpinnerGap size={16} className="animate-spin" /> : <FloppyDisk size={16} />} حفظ نطاق التقارير
                </button>}
            </>}
            {needsReload && <button type="button" onClick={loadSelection} disabled={busy}
                className="mt-3 min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700 disabled:opacity-50"
                data-testid="google-reload-account-selection">إعادة تحميل الحسابات</button>}
            {saved && <p role="status" className="mt-3 text-xs font-bold text-emerald-700">تم حفظ نطاق التقارير. ستستخدم التقارير الحسابات المحددة عند تحديثها.</p>}
        </div>}
    </section>;
}
