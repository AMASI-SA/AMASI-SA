import { useEffect, useMemo, useState } from "react";
import { CheckCircle, FloppyDisk, ImageSquare, LinkSimple, SpinnerGap, Trash, X } from "@phosphor-icons/react";
import { toast } from "sonner";

import { getProductImageProfile, saveProductImageProfile } from "../../services/mezanProductsV2";

function emptyDraft() {
    return { id: null, image_url: "", conditions: [], enabled: true };
}

function imageUrl(row) {
    return row?.thumbnail || row?.url || "";
}

function conditionLabel(condition) {
    return `${condition.option_name || condition.option_id}: ${condition.value_name || condition.value_id}`;
}

function RuleModal({ draft, options, images, busy, onClose, onChange, onCommit }) {
    if (!draft) return null;

    function updateCondition(optionId, valueId) {
        const remaining = draft.conditions.filter((row) => row.option_id !== optionId);
        if (!valueId) return onChange({ ...draft, conditions: remaining });
        const option = options.find((row) => String(row.id) === String(optionId));
        const value = (option?.values || []).find((row) => String(row.id) === String(valueId));
        onChange({
            ...draft,
            conditions: [...remaining, {
                option_id: String(optionId),
                option_name: option?.name || String(optionId),
                value_id: String(valueId),
                value_name: value?.name || String(valueId),
            }],
        });
    }

    return <div className="fixed inset-0 z-[130] flex items-center justify-center bg-slate-950/45 p-3" dir="rtl">
        <div className="max-h-[92vh] w-full max-w-3xl overflow-auto rounded-3xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b p-4 sm:p-5">
                <div><h3 className="font-black">ربط الصورة بخيارات المنتج</h3><p className="text-xs text-slate-500">حدد قيمة واحدة أو أكثر. القاعدة الأكثر تحديدًا لها الأولوية.</p></div>
                <button type="button" onClick={onClose} className="rounded-xl border p-2"><X /></button>
            </div>
            <div className="space-y-5 p-4 sm:p-5">
                <div>
                    <p className="mb-2 text-xs font-black text-slate-600">الصورة</p>
                    <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
                        {images.map((row) => {
                            const url = row.url;
                            const selected = draft.image_url === url;
                            return <button key={row.id || url} type="button" onClick={() => onChange({ ...draft, image_url: url })} className={`relative overflow-hidden rounded-xl border-2 ${selected ? "border-violet-600" : "border-transparent"}`}>
                                <img src={imageUrl(row)} alt={row.alt || ""} className="aspect-square w-full object-cover" />
                                {selected && <CheckCircle className="absolute left-2 top-2 rounded-full bg-white text-violet-700" weight="fill" />}
                            </button>;
                        })}
                    </div>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                    {options.map((option) => {
                        const selected = draft.conditions.find((row) => row.option_id === String(option.id));
                        return <label key={option.id} className="text-sm font-bold text-slate-700">{option.name}
                            <select value={selected?.value_id || ""} onChange={(event) => updateCondition(String(option.id), event.target.value)} className="mt-1 w-full rounded-xl border p-3 font-normal">
                                <option value="">لا يشترط هذا الخيار</option>
                                {(option.values || []).map((value) => <option key={value.id} value={value.id}>{value.name}</option>)}
                            </select>
                        </label>;
                    })}
                </div>
                <label className="flex items-center gap-2 text-sm font-bold"><input type="checkbox" checked={draft.enabled !== false} onChange={(event) => onChange({ ...draft, enabled: event.target.checked })} /> القاعدة مفعلة</label>
            </div>
            <div className="flex justify-end gap-2 border-t p-4">
                <button type="button" onClick={onClose} className="rounded-xl border px-4 py-2 font-bold">إلغاء</button>
                <button type="button" disabled={busy || !draft.image_url || !draft.conditions.length} onClick={onCommit} className="rounded-xl bg-violet-700 px-5 py-2 font-black text-white disabled:opacity-40">{busy ? <SpinnerGap className="inline animate-spin" /> : <FloppyDisk className="inline" />} حفظ القاعدة</button>
            </div>
        </div>
    </div>;
}

export default function ProductPreparationImageProfile({ productId }) {
    const [profile, setProfile] = useState(null);
    const [draft, setDraft] = useState(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    async function load() {
        if (!productId) return;
        setLoading(true);
        try { setProfile(await getProductImageProfile(productId)); }
        catch (error) { toast.error(error?.response?.data?.detail?.message || "تعذر تحميل صور التجهيز"); }
        finally { setLoading(false); }
    }

    useEffect(() => { setDraft(null); load(); }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps

    const images = useMemo(() => profile?.images || [], [profile]);
    const options = useMemo(() => profile?.options || [], [profile]);
    const rules = profile?.rules || [];

    async function persist(nextProfile, successMessage) {
        setSaving(true);
        try {
            const saved = await saveProductImageProfile(productId, {
                default_image_url: nextProfile.default_image_url || null,
                rules: nextProfile.rules || [],
            });
            setProfile(saved);
            setDraft(null);
            toast.success(successMessage);
        } catch (error) {
            const code = error?.response?.data?.detail?.code;
            toast.error(code === "duplicate_image_rule_conditions" ? "توجد قاعدة أخرى بنفس تركيبة الخيارات" : "تعذر حفظ صور التجهيز");
        } finally { setSaving(false); }
    }

    async function setDefault(url) {
        const clearing = profile?.default_image_url === url;
        await persist({ ...profile, default_image_url: clearing ? null : url }, clearing ? "تم إلغاء صورة ميزان الافتراضية" : "تم تعيين صورة ميزان الافتراضية للملفات");
    }

    async function commitRule() {
        if (!draft) return;
        const nextRules = draft.id
            ? rules.map((row) => row.id === draft.id ? draft : row)
            : [...rules, draft];
        await persist({ ...profile, rules: nextRules }, "تم حفظ ربط الصورة بخيارات المنتج");
    }

    async function removeRule(ruleId) {
        await persist({ ...profile, rules: rules.filter((row) => row.id !== ruleId) }, "تم حذف قاعدة الصورة");
    }

    if (loading) return <section className="rounded-2xl border p-4"><SpinnerGap className="inline animate-spin" /> جارٍ تحميل صور التجهيز…</section>;
    if (!profile) return null;

    return <section className="rounded-2xl border border-sky-200 bg-sky-50/30 p-3 sm:p-4" data-testid="product-preparation-image-profile">
        <RuleModal draft={draft} options={options} images={images} busy={saving} onClose={() => setDraft(null)} onChange={setDraft} onCommit={commitRule} />
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div><h2 className="font-black"><ImageSquare className="ml-1 inline" /> صور التجهيز — Mezan</h2><p className="text-xs text-slate-500">هذه الإعدادات للملفات والتجهيز فقط ولا تغيّر صور سلة.</p></div>
            <button type="button" disabled={saving || !options.length || !images.length} onClick={() => setDraft(emptyDraft())} className="rounded-xl bg-sky-700 px-4 py-2 font-black text-white disabled:opacity-40"><LinkSimple className="inline" /> ربط صورة بخيارات</button>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5">
            {images.map((row) => {
                const selected = profile.default_image_url === row.url;
                return <article key={row.id || row.url} className={`overflow-hidden rounded-2xl border bg-white ${selected ? "ring-2 ring-emerald-500" : ""}`}>
                    <div className="relative"><img src={imageUrl(row)} alt={row.alt || ""} className="aspect-square w-full object-cover" />{selected && <span className="absolute right-2 top-2 rounded-full bg-emerald-600 px-2 py-1 text-[10px] font-black text-white">افتراضية للملفات</span>}</div>
                    <button type="button" disabled={saving} onClick={() => setDefault(row.url)} className={`w-full px-2 py-2 text-xs font-black ${selected ? "text-rose-600" : "text-emerald-700"}`}>{selected ? "إلغاء الافتراضية" : "تعيين للملفات"}</button>
                </article>;
            })}
        </div>

        <div className="mt-5">
            <h3 className="mb-2 text-sm font-black">قواعد الخيارات ({rules.length})</h3>
            {!rules.length ? <p className="rounded-xl border border-dashed bg-white p-3 text-xs text-slate-500">لا توجد قواعد. عند عدم وجود مطابقة سيستخدم ميزان الصورة الافتراضية، ثم صورة سلة.</p> : <div className="space-y-2">{rules.map((rule) => <article key={rule.id} className="flex flex-col gap-3 rounded-2xl border bg-white p-3 sm:flex-row sm:items-center">
                <img src={rule.image_url} alt="" className="h-16 w-16 rounded-xl border object-cover" />
                <div className="min-w-0 flex-1"><div className="flex flex-wrap gap-1">{(rule.conditions || []).map((condition) => <span key={`${condition.option_id}-${condition.value_id}`} className="rounded-full bg-sky-100 px-2 py-1 text-[11px] font-bold text-sky-900">{conditionLabel(condition)}</span>)}</div><p className="mt-1 text-[11px] text-slate-500">الأولوية: {rule.condition_count || rule.conditions?.length || 0} شروط · {rule.enabled === false ? "متوقفة" : "مفعلة"}</p></div>
                <div className="flex gap-2"><button type="button" onClick={() => setDraft({ ...rule, conditions: [...(rule.conditions || [])] })} className="rounded-xl border px-3 py-2 text-xs font-bold">تعديل</button><button type="button" disabled={saving} onClick={() => removeRule(rule.id)} className="rounded-xl border border-rose-200 px-3 py-2 text-rose-600"><Trash /></button></div>
            </article>)}</div>}
        </div>
    </section>;
}
