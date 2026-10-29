import { useEffect, useRef, useState } from "react";
import { verifyGoogleStorageCommit, readGoogleStorageCommitReceipt } from "../../services/googleStorageCommitReadiness";
import { isGoogleRequestCancelled } from "../../services/googleAds";

export default function GoogleStorageCommitReadiness({ isOwner }) {
    const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
    const mounted = useRef(true), pending = useRef(false), request = useRef(null);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; request.current?.abort(); }; }, []);
    if (isOwner !== true) return null;
    async function run(readOnly) {
        if (pending.current) return;
        pending.current = true; setBusy(true); setNotice("");
        const controller = new AbortController(); request.current = controller;
        try {
            const result = await (readOnly ? readGoogleStorageCommitReceipt : verifyGoogleStorageCommit)({ signal: controller.signal });
            if (!mounted.current) return;
            const safe = result?.scope === "technical_replica_commit_readback_only" && result?.execution_authorized === false
                && result?.funding_ready === false && result?.policy_permissions_verified === false && result?.provider_writes === 0;
            if (!readOnly && safe && result.status === "verified" && result.replica_set_verified === true
                && result.commit_majority_j_ack_verified === true && result.majority_readback_verified === true && result.cleanup_verified === true) {
                setNotice("نجح حفظ السجل التقني وقراءته وتنظيفه. هذا فحص للتخزين فقط، ولا يمنح صلاحية تشغيل أو جاهزية إنفاق.");
            } else if (readOnly && safe) {
                setNotice(result.status === "historical_completed_receipt" ? "يوجد إيصال فحص سابق مكتمل؛ لا يثبت جاهزية التخزين الحالية أو صلاحية التشغيل."
                    : result.status === "no_receipt" ? "لا يوجد إيصال فحص محفوظ." : "يوجد فحص غير محسوم يحتاج تحققًا تقنيًا؛ لم يبدأ فحص جديد ولم يُحذف سجل.");
            } else setNotice("لم تُثبت جاهزية التخزين؛ لم يُمنح أي تفويض تشغيل.");
        } catch (error) {
            if (mounted.current && !isGoogleRequestCancelled(error)) setNotice("لم يكتمل الفحص. قد يبقى سجل تقني للتحقق؛ لا تُعد الحفظ أو الحذف بالتخمين. لم تتغير سياسات الحملات.");
        } finally {
            pending.current = false;
            if (mounted.current) setBusy(false);
        }
    }
    return <section aria-label="فحص حفظ التخزين التقني لجوجل" className="rounded-2xl border border-slate-200 bg-white p-4 text-sm leading-7">
        <h3 className="font-bold">فحص حفظ التخزين التقني</h3>
        <p>ينشئ الفحص سجلًا تقنيًا صغيرًا ويحفظه ويقرأه ثم ينظفه عند التأكد، مع الاحتفاظ بإيصال فحص. لا يغيّر الحملات أو حدودها.</p>
        <button type="button" disabled={busy} onClick={() => run(false)} className="rounded-lg border px-3 py-2 font-bold disabled:opacity-40">التحقق من حفظ التخزين وقراءته</button>{" "}
        <button type="button" disabled={busy} onClick={() => run(true)} className="rounded-lg border px-3 py-2 disabled:opacity-40">قراءة إيصال الفحص</button>
        {busy && <p role="status">جارٍ التحقق التقني من التخزين…</p>}
        {notice && <p role="status">{notice}</p>}
    </section>;
}
