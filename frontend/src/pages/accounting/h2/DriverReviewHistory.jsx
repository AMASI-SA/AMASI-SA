import { useEffect, useState } from "react";
import { AccountingSkeleton, EmptyState, ErrorState, MoneyDisplay, StatusBadge } from "../AccountingUI";
import { driverFailure, loadDriverHistory, reviewPresentation } from "./driverAdapter";

export default function DriverReviewHistory({ drivers }) {
    const [filters, setFilters] = useState({ driver: "", method: "", decision: "", cursor: null });
    const [page, setPage] = useState(null), [error, setError] = useState(null), [revision, setRevision] = useState(0);
    useEffect(() => {
        let current = true; setPage(null); setError(null);
        loadDriverHistory(filters).then(data => { if (current) setPage(data); })
            .catch(err => { if (current) setError(err); });
        return () => { current = false; };
    }, [filters, revision]);
    const change = (key, value) => setFilters(old => ({ ...old, [key]: value, cursor: null }));
    const retry = () => setRevision(value => value + 1);
    const failure = error && driverFailure(error);
    return <section aria-label="سجل قرارات مراجعة الموصلين" className="min-w-0 max-w-full" dir="rtl">
        <h3>سجل قرارات مراجعة الموصلين</h3>
        <p>قرارات MZ2 الأصلية لكل مراجعة ونسخة، بما فيها المرفوضة والمعتمدة. لا يشمل قرارات سابقة غير مرتبطة بدفتر MZ2، ولا يمثل رصيد النقد الفعلي أو الرصيد الحالي.</p>
        <div className="ac-actions">
            <label>موصل السجل <select value={filters.driver} onChange={e => change("driver", e.target.value)}><option value="">جميع الموصلين</option>{drivers.map(row => <option key={row.id} value={row.id}>{row.name || row.id}</option>)}</select></label>
            <label>طريقة دفع السجل <select value={filters.method} onChange={e => change("method", e.target.value)}><option value="">جميع الطرق</option><option value="bank_transfer">تحويل بنكي</option><option value="card_terminal">شبكة / POS</option></select></label>
            <label>قرار السجل <select value={filters.decision} onChange={e => change("decision", e.target.value)}><option value="">جميع القرارات</option><option value="approved">معتمد</option><option value="rejected">مرفوض</option></select></label>
            <button type="button" onClick={retry}>تحديث السجل</button>
        </div>
        {error ? <><ErrorState message={`تعذر قراءة السجل: ${failure.code}`} onRetry={retry} />{failure.blocked && <StatusBadge value="BLOCKED_BY_BACKEND" />}</> : !page ? <AccountingSkeleton label="جاري تحميل سجل قرارات المراجعة" /> : <>
            {page.coverage.unlinked_current_decisions > 0 && <div role="alert" className="h2-blocked"><p>التغطية غير مكتملة: {page.coverage.unlinked_current_decisions} مراجعة تشغيلية تتضمن قرارًا حاليًا أو سابقًا دون ربط أصلي مطابق. لا تُعرض كقرارات مالية مثبتة.</p><details><summary>المراجعات التي تحتاج تحققًا</summary>{page.coverage.unlinked_current_review_ids.map(id => <p key={id} className="break-all">{id}</p>)}</details></div>}
            {!page.items.length ? <EmptyState title="لا توجد قرارات أصلية معروضة" description="هذه نتيجة البحث في السجل فقط؛ لا تثبت انعدام الذمة أو اكتمال التاريخ السابق." /> : <div className="ac-table-scroll" tabIndex={0} role="region" aria-label="قرارات المراجعة الأصلية"><table className="ac-table"><thead><tr>{["الموصل / الطلب / النسخة", "القرار وأثره", "المبلغ والطريقة", "هوية الوجهة", "المراجع والدليل"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{page.items.map(row => <tr key={row.id}>
                <td>{drivers.find(driver => driver.id === row.driver_id)?.name || row.driver_id}<small>{row.order_number}</small><small className="break-all">{row.review_id} · نسخة {row.review_revision}</small></td>
                <td><StatusBadge value={row.status} label={reviewPresentation(row).label} /><small className="break-all">{reviewPresentation(row).effect}</small>{row.journal_reversed && <p>عُكس القيد لاحقًا؛ يبقى القرار الأصلي ضمن السجل.</p>}{row.status === "approved" && row.payment_method === "card_terminal" && <p>نُقلت مسؤولية COD إلى ذمة الشبكة. وصول المال للبنك يحتاج تسوية منفصلة.</p>}</td>
                <td><MoneyDisplay value={row.amount} /><small>{row.payment_method === "card_terminal" ? "شبكة / POS" : "تحويل بنكي"}</small></td>
                <td>{row.destination ? <><p>{row.destination.display_name || "الحساب البنكي الموثق"}</p><code className="break-all">{row.destination.entity_type} / {row.destination.entity_id} / {row.destination.sub_account}</code><details><summary>مرجع الربط الأصلي</summary><p className="break-all">{row.destination.source_namespace} / {row.destination.source_record_id} / {row.destination.source_revision}</p></details></> : "لا وجهة مالية عند الرفض"}</td>
                <td><p>{row.reviewed_by}</p><time dateTime={row.reviewed_at}>{row.reviewed_at}</time><p className="break-all">{row.receipt_reference || "مرجع غير مسجل"}</p><p className="break-words">{row.note}</p></td>
            </tr>)}</tbody></table></div>}
            {page.has_more && <button type="button" onClick={() => setFilters(old => ({ ...old, cursor: page.next_cursor }))}>القرارات الأقدم</button>}
        </>}
        {filters.cursor && <button type="button" onClick={() => setFilters(old => ({ ...old, cursor: null }))}>أحدث القرارات</button>}
    </section>;
}
