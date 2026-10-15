import { useEffect, useState } from "react";
import { AccountingSkeleton, EmptyState, ErrorState, FinancialSummaryCards, JournalTable, MoneyDisplay, StatusBadge } from "../AccountingUI";
import { DRIVER_BLOCKERS, driverFailure, loadDriverContext, loadDriverReviews, loadDriverStatement, reviewPresentation } from "./driverAdapter";
import DriverReviewHistory from "./DriverReviewHistory";
import DriverCashReconciliation from "./DriverCashReconciliation";

function Blocked({ code, children }) { return <div className="h2-blocked"><StatusBadge value="BLOCKED_BY_BACKEND" /><p>{children}</p><code style={{ overflowWrap: "anywhere", wordBreak: "break-word" }}>{code}</code></div>; }
function Failure({ error, retry }) { const failure = driverFailure(error); return failure.blocked ? <Blocked code={failure.code}>القدرة غير جاهزة من المصدر المحاسبي.</Blocked> : <ErrorState message={`تعذر قراءة البيانات: ${failure.code}`} onRetry={retry} />; }
export default function DriverPanel() {
    const [context, setContext] = useState(null), [error, setError] = useState(null), [revision, setRevision] = useState(0);
    const [reviews, setReviews] = useState(null), [reviewError, setReviewError] = useState(null);
    const [party, setParty] = useState(""), [statement, setStatement] = useState(null), [statementError, setStatementError] = useState(null);
    const [method, setMethod] = useState("");
    useEffect(() => { let current = true; setContext(null); setError(null); setReviews(null); setReviewError(null); setParty("");
        loadDriverContext().then(async data => { if (!current) return; setContext(data);
            try { const items = await loadDriverReviews(); if (current) setReviews(items); } catch (err) { if (current) setReviewError(err); }
        }).catch(err => { if (current) setError(err); }); return () => { current = false; }; }, [revision]);
    useEffect(() => { let current = true; setStatement(null); setStatementError(null); if (party) {
        const [kind, identity] = JSON.parse(party); loadDriverStatement(kind, identity).then(data => { if (current) setStatement(data); }).catch(err => { if (current) setStatementError(err); });
    } return () => { current = false; }; }, [party, revision]);
    const retry = () => setRevision(value => value + 1);
    if (error) return <Failure error={error} retry={retry} />;
    if (!context) return <AccountingSkeleton label="جاري تحميل مسؤوليات الموصلين والشحن" />;
    const parties = [...context.store_drivers.map(row => ({ kind: "store_driver", id: row.id, name: row.name })), ...context.couriers.map(row => ({ kind: "courier", id: row.courier_key, name: row.name }))];
    const visible = reviews?.filter(row => !method || row.payment_method === method);
    return <section dir="rtl" aria-label="الموصلون والشحن والتحويلات والشبكة">
        <h3>الموصلون والشحن والتحويلات والشبكة</h3>
        <button type="button" onClick={retry}>تحديث بيانات الشحن</button>
        <p>مسؤولية COD والمستحق مستقلة. الأرقام التالية من دفتر MZ2 فقط.</p>{Object.entries(context.stages || {}).filter(([, stage]) => stage.ready !== true).map(([id, stage]) => <Blocked key={id} code={(stage.reasons || []).map(reason => reason.code).filter(Boolean).join(" / ") || "shipping_stage_readiness_missing"}>متطلبات تهيئة الشحن / المرحلة {id} غير مكتملة حسب Backend.</Blocked>)}
        <label>كشف الموصل أو شركة الشحن <select value={party} onChange={event => setParty(event.target.value)}><option value="">اختر هوية من MZ2</option>{parties.filter(row => row.id).map(row => <option key={`${row.kind}:${row.id}`} value={JSON.stringify([row.kind, row.id])}>{row.name || row.id} · {row.kind === "courier" ? "شركة شحن" : "موصل"}</option>)}</select></label>
        {!parties.length && <EmptyState title="لا توجد هويات جاهزة" description="لم يُرجع المصدر موصلين أو شركات شحن مؤكدة." />}
        {party && !statement && !statementError && <AccountingSkeleton label="جاري تحميل الكشف الأصلي" />}
        {statementError && <Failure error={statementError} retry={retry} />}
        {statement && <><FinancialSummaryCards items={[{ label: "مسؤولية COD المتبقية", value: statement.cod_receivable, money: true }, { label: "مستحق أجور التوصيل", value: statement.payable, money: true }, { label: "التحصيلات المثبتة", value: statement.collections, money: true }, { label: "المدفوعات المثبتة", value: statement.payments, money: true }]} />{Array.isArray(statement.entries) && statement.entries.length ? <JournalTable rows={statement.entries} label="حركات الموصل أو شركة الشحن الأصلية" /> : <EmptyState title="لا توجد حركات معروضة" description="الرصيد أعلاه كما أعاده Backend؛ لا يُحسب من الجدول." />}</>}
        <h3>مراجعة التحويل البنكي والشبكة</h3>
        <label>طريقة الدفع <select value={method} onChange={event => setMethod(event.target.value)}><option value="">كل طلبات المراجعة</option><option value="bank_transfer">تحويل بنكي</option><option value="card_terminal">شبكة / POS</option></select></label>
        <p>تُعرض أول 250 مراجعة معلقة فقط. مبالغ الأدلة ليست أرصدة أو تحصيلات نهائية.</p>
        {reviewError ? <Failure error={reviewError} retry={retry} /> : !reviews ? <AccountingSkeleton label="جاري تحميل المراجعات المعلقة" /> : !visible.length ? <EmptyState title="لا توجد مراجعات معلقة معروضة" description="لا تعني قائمة المراجعة الفارغة أن مسؤولية الموصل صفر." /> : <div className="ac-table-scroll" tabIndex={0} role="region" aria-label="طلبات مراجعة الموصلين"><table className="ac-table"><thead><tr>{["الموصل / الطلب", "الطريقة", "مبلغ الدليل", "الحالة", "المرجع"].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{visible.map((row, index) => <tr key={row.id || index}><td>{row.driver_name || row.driver_id}<small>{row.order_number}</small></td><td>{row.payment_method === "bank_transfer" ? "تحويل بنكي" : row.payment_method === "card_terminal" ? "شبكة / POS" : row.payment_method}</td><td><MoneyDisplay value={row.amount} /></td><td><StatusBadge value={row.status} label={reviewPresentation(row).label} /><small>{reviewPresentation(row).effect}</small></td><td>{row.receipt_reference || "غير متاح"}</td></tr>)}</tbody></table></div>}
        {context.driver_payment_destination?.methods?.bank_transfer?.adapter_connected === true
            ? <p data-testid="driver-bank-proof-requirement">ربط إثبات التحويل البنكي متاح. يبقى الاعتماد مشروطًا بحركة وصول بنكية موثقة وهوية مطابقة، مع جميع ضوابط الافتتاحية والتفعيل والإيقاف والصلاحيات.</p>
            : <Blocked code="driver_bank_destination_readiness_required">اعتماد التحويل البنكي ينتظر ربط وجهة مالية ودليل وصول موثقين من Backend.</Blocked>}
        {context.driver_payment_destination?.methods?.card_terminal?.adapter_connected === true && context.driver_payment_destination.methods.card_terminal.evidence_required === "accountant_reviewed_pos_receipt"
            ? <p data-testid="driver-pos-proof-requirement">اعتماد الشبكة متاح بعد مراجعة المحاسب للإيصال المرتبط ومطابقة المبلغ واختيار ذمة أصلية موثقة صراحةً. ينقل الاعتماد مسؤولية COD إلى ذمة الشبكة المختارة ضمن الذمم المدينة الأخرى، ولا يعني وصول المبلغ إلى البنك. تبقى ضوابط الافتتاحية والتفعيل والإيقاف والصلاحيات مطلوبة.</p>
            : <Blocked code={context.driver_payment_destination?.methods?.card_terminal?.code || context.driver_payment_destination?.code || "driver_payment_destination_readiness_required"}>اعتماد الشبكة ينتظر ربط متطلبات مراجعة الإيصال والوجهة الأصلية من Backend. اعتماد الشبكة لا يعني وصول المبلغ إلى البنك.</Blocked>}
        {context.bank_port.ready !== true && <Blocked code={context.bank_port.code || "shipping_bank_binding_readiness_required"}>تحصيل النقد وسداد الموصلين والشحن وتسوية الشبكة إلى البنك بانتظار الربط البنكي الأصلي.</Blocked>}
        {context.driver_review_history?.ready === true && context.driver_review_history.scope === "native_v2_decisions_only"
            ? <DriverReviewHistory key={revision} drivers={context.store_drivers} />
            : <Blocked code={DRIVER_BLOCKERS.history}>سجل المراجعات المعتمدة والمرفوضة: عقد قراءة السجل غير متاح من المصدر.</Blocked>}
        {context.driver_physical_cash?.ready === true && context.driver_physical_cash.scope === "captured_delivered_cash_only"
            ? party && JSON.parse(party)[0] === "store_driver"
                ? <DriverCashReconciliation key={`${party}:${revision}`} driverId={JSON.parse(party)[1]} />
                : <p>اختر موصلًا لعرض النقد الفعلي الذي أكده وربطه بالتوريدات القائمة.</p>
            : <Blocked code={DRIVER_BLOCKERS.cash}>حيازة النقد الفعلية: لا يوفر المصدر كشفًا أصليًا مستقلاً؛ لا تُستنتج من مسؤولية COD.</Blocked>}
    </section>;
}
