import { useEffect, useRef, useState } from "react";
import { readGoogleNamespacePrerequisites } from "../../services/googleNamespacePrerequisites";
import { isGoogleRequestCancelled } from "../../services/googleAds";
const ACTIONS = { find: "قراءة السجل", insert: "إضافة السجل", update: "تحديث السجل" };
const LABELS = { standing_policy: "إعدادات الإدارة", creation_policy: "إعدادات الإنشاء", account_guard: "حارس حدود الحساب", engine_probe: "فحص التخزين" };
export default function GoogleNamespacePrerequisites({ isOwner }) {
    const [busy, setBusy] = useState(false), [result, setResult] = useState(null), [error, setError] = useState("");
    const mounted = useRef(true), pending = useRef(false), request = useRef(null);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
    if (isOwner !== true) return null;
    async function read() {
        if (pending.current) return;
        pending.current = true; setBusy(true); setError(""); setResult(null);
        const controller = new AbortController(); request.current = controller;
        try {
            const data = await readGoogleNamespacePrerequisites({ signal: controller.signal });
            if (!mounted.current) return;
            if (data?.scope !== "namespace_and_legacy_read_only" || data?.engine_commit_verified !== false || data?.migration_ready !== false
                || data?.execution_authorized !== false || data?.funding_ready !== false || data?.business_writes !== 0 || data?.provider_writes !== 0) {
                setError("لم تُثبت متطلبات الحفظ؛ لم تتغير السياسات أو صلاحيات التشغيل."); return;
            }
            setResult(data);
        } catch (failure) {
            if (mounted.current && !isGoogleRequestCancelled(failure)) setError("تعذر إثبات متطلبات الحفظ بالقراءة؛ لم تتغير السياسات أو صلاحيات التشغيل.");
        } finally { pending.current = false; if (mounted.current) setBusy(false); }
    }
    return <details aria-label="متطلبات حفظ حدود جوجل" className="rounded-2xl border border-slate-200 bg-white p-4 text-sm leading-7">
        <summary className="cursor-pointer font-bold">متطلبات حفظ حدود جوجل</summary>
        <p>فحص قراءة للمالك فقط. لا يثبت الحفظ الفعلي أو الالتزامات الإعلانية ولا يفعّل الحملات.</p>
        <button type="button" disabled={busy} onClick={read} className="rounded-lg border px-3 py-2 font-bold disabled:opacity-40">فحص المتطلبات بالقراءة</button>
        {busy && <p role="status">جارٍ قراءة المتطلبات…</p>}
        {error && <p role="alert">{error}</p>}
        {result && <>
            <p role="status">{result.namespace_prerequisites_verified === true ? "المتطلبات المقروءة مثبتة لهذه اللحظة فقط؛ لا يمنح الفحص تفويض تشغيل." : "بعض المتطلبات غير مثبتة؛ لم تتغير السياسات أو صلاحيات التشغيل."}</p>
            <details className="mt-2"><summary className="cursor-pointer">تفاصيل تقنية للمراجعة</summary>
                {Object.entries(LABELS).map(([key, label]) => {
                    const row = result.namespaces?.[key];
                    return <section key={key} className="mt-2 border-t pt-2"><h4 className="font-bold">{label}</h4>
                        <p>الصلاحيات المطلوبة: {row?.required_actions_verified === true ? "مثبتة" : "غير مثبتة"} · بنية السجل: {row?.collection_contract_verified === true ? "مثبتة" : "غير مثبتة"} · الفهرس: {row?.unique_index_verified === true ? "مثبت" : "غير مثبت"}</p>
                        <p>{Object.entries(ACTIONS).filter(([action]) => row?.actions_verified && action in row.actions_verified).map(([action, text]) => `${text}: ${row.actions_verified[action] === true ? "مثبتة" : "غير مثبتة"}`).join(" · ")}</p>
                        <p>اكتمال القراءة: {row?.inventory_complete === true ? "مثبت" : "غير مثبت"} · سجلات مفحوصة: {Number.isInteger(row?.records_checked) ? row.records_checked : 0} · مخالفات بنية: {Number.isInteger(row?.malformed_count) ? row.malformed_count : 0} · تكرار نطاق: {Number.isInteger(row?.duplicate_scope_count) ? row.duplicate_scope_count : 0}</p>
                        {row?.create_index_for_put_verified !== undefined && <p>صلاحية تجهيز فهرس الحفظ: {row.create_index_for_put_verified === true ? "مثبتة" : "غير مثبتة"}؛ لا تُضاف إلى شروط إلغاء التفويض.</p>}
                    </section>;
                })}
                <p className="mt-2">لا يشمل هذا الفحص إثبات الحفظ الفعلي أو ربط الالتزامات القديمة، ولا يغيّر شروط إلغاء التفويض.</p>
            </details>
        </>}
    </details>;
}
