import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

function midpoint(from, to) {
    const start = Date.parse(`${from}T00:00:00Z`);
    const end = Date.parse(`${to}T00:00:00Z`);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return "";
    return new Date(start + Math.floor(((end - start) / 86400000 + 1) / 2) * 86400000).toISOString().slice(0, 10);
}

export default function SnapchatPeriodDiagnostic({ accountId, dateFrom, dateTo, disabled }) {
    const [split, setSplit] = useState(() => midpoint(dateFrom, dateTo));
    const [result, setResult] = useState(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const request = useRef(0);
    useEffect(() => () => { request.current += 1; }, []);
    const valid = accountId && dateFrom < split && split <= dateTo;

    async function run() {
        if (!valid || busy || disabled) return;
        const current = ++request.current;
        setBusy(true);
        setError("");
        setResult(null);
        try {
            const { data } = await api.get("/integrations-v2/snapchat-v2/period-diagnostics", {
                params: { ad_account_id: accountId, date_from: dateFrom, date_to: dateTo, split_on: split },
                timeout: 25000,
            });
            if (current !== request.current) return;
            if (data.ad_account_id !== accountId) throw new Error("account_changed");
            setResult(data);
        } catch {
            if (current === request.current) setError("لم يكتمل الفحص. لا توجد نتيجة معتمدة؛ تحقق من الحساب أو جرّب فترة أقصر.");
        } finally {
            if (current === request.current) setBusy(false);
        }
    }

    return <details className="rounded-xl border border-slate-200 bg-white p-4" data-testid="snapchat-period-diagnostic">
        <summary className="cursor-pointer font-bold">مراجعة اتساق فترة الطلبات</summary>
        <p className="my-3 text-sm text-slate-600">يقارن الفترة المختارة بمجموع جزأين منها، ويكشف الطلبات المستبعدة عند التقسيم بسبب اختلاف التواريخ. لا يغيّر بيانات الطلبات.</p>
        <p className="text-sm" dir="ltr">{dateFrom} — {dateTo}</p>
        <div className="my-3 flex flex-wrap items-end gap-3">
            <label className="text-sm">بداية الجزء الثاني
                <input type="date" aria-label="بداية الجزء الثاني" value={split} max={dateTo} disabled={busy || disabled}
                    onChange={event => { setSplit(event.target.value); setResult(null); setError(""); }}
                    className="mr-2 rounded-lg border p-2" />
            </label>
            <button type="button" onClick={run} disabled={!valid || busy || disabled} className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40">
                {busy ? "جارٍ فحص الفترة…" : "فحص اتساق الفترة"}
            </button>
        </div>
        {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        {result && <div role="status" className="space-y-3 text-sm">
            <p>توقيت المقارنة: <span dir="ltr">{result.timezone}</span></p>
            <div className="overflow-x-auto"><table className="w-full text-right">
                <thead><tr><th>الفترة</th><th>كل الطلبات</th><th>مصدرها سناب صراحةً</th></tr></thead>
                <tbody>{[["whole", "الفترة الكاملة"], ["left", "الجزء الأول"], ["right", "الجزء الثاني"], ["gap", "فرق العدّ"]].map(([key, label]) =>
                    <tr key={key}><th className="py-2">{label}{result.periods[key] && <span className="block font-normal" dir="ltr">{result.periods[key].join(" — ")}</span>}</th><td>{result.all_orders[key]}</td><td>{result.explicit_snapchat_source[key]}</td></tr>)}</tbody>
            </table></div>
            <p>يفحص الطلبات التي يجلبها التقرير للفترة الكاملة فقط. عمود سناب يعتمد على مصدر الطلب الصريح؛ لا يقيس نسبة الربط بالحملات.</p>
        </div>}
    </details>;
}
