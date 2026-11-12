import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import PaginatedReportTable from "./PaginatedReportTable";
import { deliveryOrderLabel, formatPositionCount } from "./financialPositionPresentation";
import "./financialPosition.css";

export function distinctPendingOrders(details = []) {
    const orders = new Map();
    for (const item of Array.isArray(details) ? details : []) {
        const id = item?.order_id || item?.order_number;
        if (!id) continue;
        const key = String(id);
        const row = orders.get(key) || { ...item, order_id: item.order_id, reasons: [] };
        if (!row.order_number && item.order_number) row.order_number = item.order_number;
        for (const reason of item.reasons || []) {
            if (reason?.message && !row.reasons.some(existing => existing.code === reason.code && existing.message === reason.message)) {
                row.reasons.push(reason);
            }
        }
        orders.set(key, row);
    }
    return [...orders.values()];
}

export default function PendingOrdersDialog({ count, details, name = "هذه الجهة" }) {
    const [open, setOpen] = useState(false);
    const [search, setSearch] = useState("");
    const dialogRef = useRef(null), triggerRef = useRef(null);
    const allRows = useMemo(() => distinctPendingOrders(details), [details]);
    const rows = useMemo(() => allRows.filter(row => !search.trim() ||
        [row.order_number, row.order_id].some(value => String(value || "").includes(search.trim()))), [allRows, search]);
    useEffect(() => {
        const dialog = dialogRef.current;
        if (!open || !dialog) return;
        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "");
        dialog.querySelector("input")?.focus();
        return () => { if (typeof dialog.close === "function") dialog.close(); triggerRef.current?.focus(); };
    }, [open, count]);
    const close = () => { setOpen(false); setSearch(""); };
    if (!Number.isSafeInteger(count) || count <= 0) return <bdi>{formatPositionCount(count)}</bdi>;
    return <>
        <button ref={triggerRef} type="button" className="ofp-pending-orders" onClick={() => setOpen(true)} aria-label={`${count} طلبات غير مؤكدة — ${name}`}>
            <bdi>{formatPositionCount(count)}</bdi> طلبات غير مؤكدة
        </button>
        {open && createPortal(<dialog ref={dialogRef} className="ofp-order-review ofp-no-print" dir="rtl" role="dialog" aria-modal="true" aria-label={`طلبات غير مؤكدة — ${name}`}
            onCancel={event => { event.preventDefault(); close(); }} onClick={event => { if (event.target === event.currentTarget) close(); }}>
            <div><div className="ofp-review-header"><h2>طلبات غير مؤكدة — {name}</h2>
                    <button type="button" className="ofp-button" aria-label="إغلاق نافذة الطلبات" onClick={close}>إغلاق</button></div>
                <p>عدد الطلبات التي تحتاج مراجعة: <bdi>{formatPositionCount(count)}</bdi>. قد يكون للطلب أكثر من سبب، ويُحسب مرة واحدة. تبقى المبالغ المثبتة ضمن الرصيد المحتسب.</p>
                <label className="ofp-review-search">البحث برقم الطلب
                    <input value={search} type="search" onChange={event => setSearch(event.target.value)} aria-label="البحث في الطلبات غير المؤكدة" />
                </label>
                <PaginatedReportTable rows={rows} pageSize={10} label="الطلبات غير المؤكدة" resetKey={search}>
                    {page => <div className="ofp-table-scroll"><table><thead><tr><th scope="col">رقم الطلب</th><th scope="col">سبب عدم التأكد</th></tr></thead>
                        <tbody>{page.map((row, index) => <tr key={row.order_id || row.order_number || index}>
                            <td><bdi>{deliveryOrderLabel(row)}</bdi></td>
                            <td>{row.reasons.length ? row.reasons.map((reason, reasonIndex) => <p key={`${reason.code}:${reasonIndex}`}>{reason.message}</p>) : "سبب المراجعة غير متوفر في هذه اللقطة."}</td>
                        </tr>)}</tbody></table></div>}
                </PaginatedReportTable>
                {!rows.length && <p role="status">{allRows.length ? "لا توجد طلبات تطابق البحث." : "تفاصيل الطلبات غير متوفرة في هذه اللقطة؛ حدّث التقرير لقراءة أحدث تفاصيل محفوظة."}</p>}
                {allRows.length !== count && <p role="status">تفاصيل متاحة لـ{allRows.length} من {count} طلبًا. لم تُعتبر بقية الطلبات مكتملة.</p>}
            </div></dialog>, document.body)}
    </>;
}
