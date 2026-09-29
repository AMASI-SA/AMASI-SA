import { useState } from "react";
import { toast } from "sonner";
import { saveOpeningProviderBankBinding } from "../../services/accountingModule";

export default function OpeningProviderBindings({ context, canManage, evidenceFileId, onSaved }) {
    const [edits, setEdits] = useState({});
    const [busy, setBusy] = useState("");
    const providers = context?.entities?.provider_receivable || [];
    async function save(provider) {
        const choice = edits[provider] || {};
        if (!canManage || !choice.bank || !choice.confirmed || !evidenceFileId) return;
        setBusy(provider);
        try {
            await saveOpeningProviderBankBinding(provider, {
                bank_account_id: choice.bank, evidence_ref: evidenceFileId, confirmed: true,
            });
            toast.success("حُفظ ربط المزود بالبنك دون أي قيد مالي");
            await onSaved();
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error(detail?.message || detail?.code || "تعذر حفظ ربط البنك");
        } finally { setBusy(""); }
    }
    return <section className="space-y-3 rounded-xl border border-slate-200 bg-white p-4"
        data-testid="opening-provider-bindings">
        <h3 className="font-bold">البنك الفعلي لاستلام تسويات المزود</h3>
        <p className="text-sm text-slate-600">مدى وApple Pay تبقيان ضمن سلة عندما تكون التسوية عبرها. لا يُسجل رصيد إضافي لهما.</p>
        {!evidenceFileId && canManage && <p className="text-sm text-amber-800">ارفع دليل قسم المزودين أدناه قبل تأكيد الربط.</p>}
        {providers.map((provider) => {
            const binding = (context?.provider_bindings || []).find((item) => item.provider === provider.id);
            const edit = edits[provider.id] || {};
            const patch = (value) => setEdits((current) => ({
                ...current, [provider.id]: { ...current[provider.id], ...value },
            }));
            return <div key={provider.id} className="flex flex-wrap items-center gap-3 border-t pt-3">
                <strong>{provider.name || provider.id}</strong>
                <span className="text-xs">{binding?.configured && !binding?.needs_confirmation
                    ? binding.bank_account_name : "الربط يحتاج تأكيدًا بحساب مالي معرف"}</span>
                {canManage && <>
                    <select aria-label={`بنك ${provider.id}`} value={edit.bank || ""}
                        onChange={(event) => patch({ bank: event.target.value, confirmed: false })}
                        className="rounded border p-2 text-sm">
                        <option value="">اختر البنك المستلم</option>
                        {(context?.banks || []).map((bank) => <option key={bank.id} value={bank.id}>{bank.name}</option>)}
                    </select>
                    <label className="text-xs"><input type="checkbox" checked={Boolean(edit.confirmed)}
                        onChange={(event) => patch({ confirmed: event.target.checked })}
                        aria-label={`تأكيد بنك ${provider.id}`} /> أؤكد البنك وفق الدليل</label>
                    <button type="button" onClick={() => save(provider.id)}
                        disabled={!edit.bank || !edit.confirmed || !evidenceFileId || Boolean(busy)}
                        className="rounded border px-3 py-2 text-xs disabled:opacity-40">حفظ ربط {provider.name || provider.id}</button>
                </>}
            </div>;
        })}
        {(context?.warnings || []).map((warning, index) => <p key={warning.code || index} role="status"
            className="text-sm text-amber-800">{warning.message || warning.code}</p>)}
        <p className="text-xs text-slate-600">دفعات المورد المقدمة غير المدعومة تحتاج معالجة مستقلة؛ لا تخصمها من مستحق المورد ولا تسجلها كصافي رصيد.</p>
    </section>;
}
