import { useState } from "react";
import api from "../../lib/api";
import { useOptionalAuth } from "../../context/AuthContext";
import { authIdentity, useBalanceRead } from "./simpleBalancesHooks";
import OperationalToolBack from "./OperationalToolBack";
import "./operationalToolTheme.css";

const reports = {
    "balances.returns": "returns", "balances.exchanges": "exchanges", "balances.fees": "fees",
};

export default function EmployeeBalanceSpecialist({ page }) {
    const auth = useOptionalAuth();
    const [number, setNumber] = useState(1);
    const kind = reports[page.id];
    const key = JSON.stringify([authIdentity(auth), page.id, number]);
    const result = useBalanceRead(async config => {
        const { data } = await api.get("/operational-balances/workspace/specialist/" + kind, {
            ...config, params: { page: number },
        });
        if (!Array.isArray(data?.items) || data.items.length > 8 || data.page !== number || data.page_size !== 8
            || !Number.isInteger(data.total_pages) || data.total_pages < 0) throw new Error("Invalid specialist report");
        return data;
    }, key, Boolean(kind && auth?.user?.id && !auth.loading));
    return <section className="mz-finance-tool space-y-4 rounded-2xl border bg-white p-5" dir="rtl">
        <OperationalToolBack/>
        <h1 className="text-2xl font-bold">{page.label}</h1>
        {result.error ? <p role="alert">{result.error} <button type="button" className="underline" onClick={result.reload}>إعادة المحاولة</button></p>
            : !result.data ? <p role="status">جارٍ تحميل السجل…</p>
            : result.data.items.length ? <div className="overflow-x-auto">
                <table className="w-full text-right text-sm"><thead><tr>{["البيان", "التفاصيل", "الحالة", "التاريخ"].map(title =>
                    <th key={title} scope="col" className="border-b p-3">{title}</th>)}</tr></thead>
                    <tbody>{result.data.items.map(row => <tr key={row.id}>{[row.label, row.detail, row.status, row.date].map((value,index)=>
                        <td key={index} className="border-b p-3">{value || "—"}</td>)}</tr>)}</tbody>
                </table></div> : <p>لا توجد سجلات متاحة ضمن صلاحياتك.</p>}
        {(number > 1 || result.data?.total_pages > 1) && <nav aria-label="صفحات السجل" className="flex items-center gap-4">
            <button type="button" disabled={number < 2} onClick={() => setNumber(current=>current-1)}>السابق</button>
            <span>صفحة {number}{result.data?.total_pages ? " من " + result.data.total_pages : ""}</span>
            <button type="button" disabled={!result.data || number >= result.data.total_pages} onClick={() => setNumber(current=>current+1)}>التالي</button>
        </nav>}
    </section>;
}
