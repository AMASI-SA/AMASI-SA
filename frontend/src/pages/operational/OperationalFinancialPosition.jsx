import { useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { ArrowClockwise, CalendarBlank, Info, MagnifyingGlass, MicrosoftExcelLogo, Printer, WarningCircle } from "@phosphor-icons/react";
import { useReactToPrint } from "react-to-print";
import { useOptionalAuth } from "../../context/AuthContext";
import XLSX from "../../lib/safeSpreadsheet";
import { fileStampSA, formatRiyadhArabicLong, formatRiyadhDateTime } from "../../lib/dates";
import { operationalApi } from "./api";
import {
    buildFinancialPositionWorkbook, collectionLabel, currencyCodes, DELIVERY_MONEY_COLUMNS, DELIVERY_SCOPE_NOTE,
    deliveryOrderLabel, deliveryParties, destinationLabel, filterDeliveries, formatPositionCount, formatPositionMoney,
    isPositionContract, PARTY_LABELS, paymentLabel, positionText, readableMessages, shippingRecognitionLabel, UNAVAILABLE,
} from "./financialPositionPresentation";
import "./financialPosition.css";
import NativePurchaseReview from "./NativePurchaseReview";
import HistoricalShippingFeeReview from "./HistoricalShippingFeeReview";
import ProviderHistoryReview from "./ProviderHistoryReview";
import PaginatedReportTable from "./PaginatedReportTable";
import CustomerWalletBalance from "./CustomerWalletBalance";
import PendingOrdersDialog from "./PendingOrdersDialog";
import CompletedShippingFeeReview from "./CompletedShippingFeeReview";
import { visibleOperationalRead, refreshVisibleSnapshot } from "./visibleOperationalRead";

const emptyFilters = () => ({ order: "", party: "", payment: "" });
const inactiveMessages = {
    draft: "يظهر التقرير المالي التشغيلي بعد اعتماد الأرصدة الافتتاحية وبدء التشغيل.",
    frozen: "التشغيل مجمّد حاليًا؛ التقرير الحالي غير متاح.",
    accounting_active: "بدأ النظام المحاسبي؛ التقرير التشغيلي الحالي غير متاح ضمن هذه المرحلة.",
};

function Money({ value, role = "neutral" }) {
    const text = formatPositionMoney(value);
    const tone = text === UNAVAILABLE ? "unavailable" : String(value).startsWith("-") ? "negative" : role === "receivable" ? "positive" : role === "payable" ? "negative" : "neutral";
    return <bdi dir="ltr" className={`ofp-money ofp-${tone}`} aria-label={text === UNAVAILABLE ? "المبلغ غير متاح" : undefined}>{text}</bdi>;
}

function Notes({ items, className = "" }) {
    const notes = readableMessages(items || []);
    return notes.length ? <div className={`ofp-notes ${className}`}>{notes.map(note => <p key={note}>{note}</p>)}</div> : null;
}

function SectionHeading({ id, number, title, children }) {
    return <div className="ofp-section-heading"><h2 id={id}><span className="ofp-section-number">{number}</span>{title}</h2><div className="ofp-legend">{children}</div></div>;
}

function DataCell({ value, column }) {
    if (column.type === "money") return <Money value={value} role={column.role} />;
    if (["count", "exceptions"].includes(column.type)) return <bdi className="ofp-count" dir="ltr">{formatPositionCount(value)}</bdi>;
    if (column.key === "as_of" && value) return formatRiyadhDateTime(value);
    return positionText(value);
}

function ReportTable({ table, printing }) {
    const columns = table.columns || [];
    const hideInPrint = table.kind === "review" || ["provider_collections_expected", "cod_expected", "shipping_expected"].includes(table.id);
    return <article className={`ofp-table-card${["providers", "ads_currency_conversion", "customer_wallets"].includes(table.id) ? " ofp-table-wide" : ""}${table.kind === "forecast" ? " ofp-table-forecast" : ""}${hideInPrint ? " ofp-no-print" : ""}`} data-testid={`financial-table-${table.id}`}>
        <div className="ofp-table-title"><h3>{table.title}</h3>{table.complete !== true && <span className="ofp-status">غير مكتمل</span>}</div>
        <PaginatedReportTable rows={table.rows || []} label={table.title} pageSize={table.id === "provider_collections_expected" ? 10 : 13} showAll={printing && !hideInPrint}>{rows => <div className="ofp-table-scroll"><table>
            <thead><tr>{columns.map(column => <th scope="col" key={column.key}>{column.label}</th>)}</tr></thead>
            <tbody>{rows.map((row, index) => <tr key={row.id || index}>{columns.map(column =>
                <td key={column.key} className={column.type === "text" ? "ofp-label-cell" : "ofp-value-cell"}>{column.type === "exceptions" && !printing
                    ? <PendingOrdersDialog count={row[column.key]} details={row.pending_order_details} name={row.name || table.title} />
                    : <DataCell value={row[column.key]} column={column} />}</td>)}</tr>)}
                {!table.rows?.length && <tr><td className="ofp-empty-cell" colSpan={Math.max(columns.length, 1)}>{table.complete === true ? "لا توجد أرصدة مسجلة لهذا القسم." : "لم تتوفر بيانات مكتملة لهذا القسم."}</td></tr>}
            </tbody>
            {table.show_totals !== false && table.totals && Object.keys(table.totals).length > 0 && <tfoot><tr>{columns.map((column, index) => <td key={column.key}>
                {index === 0 && column.type === "text" ? "الإجمالي" : <DataCell value={table.totals[column.key]} column={column} />}
            </td>)}</tr></tfoot>}
        </table></div>}</PaginatedReportTable>
        {table.unassigned_order_details?.length > 0 && <div className="ofp-no-print"><PendingOrdersDialog count={table.unassigned_order_details.length} details={table.unassigned_order_details} name="طلبات لم تثبت جهة توصيلها" /></div>}
        <Notes items={table.notes} />
    </article>;
}

function DebtTable({ data, printing }) {
    return <article className="ofp-table-card ofp-debts" data-testid="financial-debt-summary"><div className="ofp-table-title"><h3>إجمالي الديون والمستحقات</h3>{data.completeness?.actual !== true && <span className="ofp-status">الأرصدة المسجّلة فقط</span>}</div>
        <PaginatedReportTable rows={data.debt_rows || []} label="إجمالي الديون والمستحقات" showAll={printing}>{rows => <table><thead><tr><th scope="col">الجهة</th><th scope="col">لنا</th><th scope="col">علينا</th></tr></thead>
            <tbody>{rows.map(row => <tr key={row.id}><td className="ofp-label-cell">{row.name}</td><td><Money value={row.receivable} role="receivable" /></td><td><Money value={row.payable} role="payable" /></td></tr>)}</tbody>
            <tfoot><tr><td>الإجمالي</td><td><Money value={data.debt_totals?.receivable} role="receivable" /></td><td><Money value={data.debt_totals?.payable} role="payable" /></td></tr></tfoot>
        </table>}</PaginatedReportTable><Notes items={["السيولة والأرصدة الإعلانية المقدمة وعهد الموظفين تظهر مستقلة عن الديون."]} />
    </article>;
}

function AmountTable({ title, rows = [], className = "", complete, printing }) {
    return <article className={`ofp-table-card ${className}`}><div className="ofp-table-title"><h3>{title}</h3>{complete === false && <span className="ofp-status">غير مكتمل</span>}</div>
        <PaginatedReportTable rows={rows} label={title} showAll={printing}>{pageRows => <table><thead><tr><th scope="col">البند</th><th scope="col">المبلغ</th></tr></thead><tbody>
            {pageRows.map(row => <tr key={row.id} className={["total", "positive", "negative"].includes(row.emphasis) ? `ofp-row-${row.emphasis}` : ""}>
                <td className="ofp-label-cell">{row.label}{row.reason && <small className="ofp-row-reason">{row.reason}</small>}</td>
                <td><Money value={row.amount} role={row.emphasis === "positive" ? "receivable" : row.emphasis === "negative" ? "payable" : "neutral"} />
                    {row.amount == null && row.known_subtotal != null && <small className="ofp-row-reason">المجموع المعلوم: <Money value={row.known_subtotal} /></small>}</td>
            </tr>)}
            {!rows.length && <tr><td className="ofp-empty-cell" colSpan="2">لم تتوفر بيانات مكتملة لهذا القسم.</td></tr>}
        </tbody></table>}</PaginatedReportTable>
    </article>;
}

function CollectionStatus({ value }) {
    return <span className={`ofp-collection-status ${value === "confirmed" ? "ofp-confirmed" : value === "pending_review" ? "ofp-pending" : ""}`}>{collectionLabel(value)}</span>;
}

function DeliveryDetails({ delivery, filters, onFiltersChange, currency }) {
    const allRows = Array.isArray(delivery?.rows) ? delivery.rows : [];
    const rows = filterDeliveries(allRows, filters);
    const parties = deliveryParties(allRows);
    const payments = [...new Set(allRows.map(row => row.payment_method || "unavailable"))];
    const update = patch => onFiltersChange({ ...filters, ...patch });
    return <article className="ofp-deliveries ofp-no-print" data-testid="financial-deliveries">
        <div className="ofp-detail-heading"><div><h3>تفاصيل الشحن والتحصيل لكل طلب</h3><p>أجرة التنفيذ وحالة التحصيل ومكان المبلغ لكل طلب · {currency}</p></div>{delivery?.complete !== true && <span className="ofp-status">تفاصيل غير مكتملة</span>}</div>
        <div className="ofp-delivery-filters ofp-no-print" aria-label="فلاتر تفاصيل الطلبات">
            <label><span>رقم الطلب أو معرفه</span><div className="ofp-search"><MagnifyingGlass size={18} aria-hidden="true" /><input type="search" aria-label="البحث برقم الطلب" placeholder="ابحث برقم الطلب أو معرفه" value={filters.order} onChange={event => update({ order: event.target.value })} /></div></label>
            <label><span>جهة التنفيذ</span><select aria-label="تصفية جهة التنفيذ" value={filters.party} onChange={event => update({ party: event.target.value })}><option value="">كل الجهات</option>{parties.map(party => <option key={party.key} value={party.key}>{party.label}</option>)}</select></label>
            <label><span>طريقة الدفع</span><select aria-label="تصفية طريقة الدفع" value={filters.payment} onChange={event => update({ payment: event.target.value })}><option value="">كل الطرق</option>{payments.map(payment => <option key={payment} value={payment}>{paymentLabel(payment)}</option>)}</select></label>
            <button type="button" className="ofp-button" onClick={() => onFiltersChange(emptyFilters())}>مسح الفلاتر</button>
        </div>
        <p className="ofp-detail-scope">السجلات المعروضة: <bdi>{rows.length}</bdi> من <bdi>{allRows.length}</bdi>. فلاتر التفاصيل لا تغيّر إجماليات التقرير، ويشمل التصدير التفاصيل المعروضة.</p>
        <PaginatedReportTable rows={rows} label="تفاصيل الشحن والتحصيل لكل طلب" resetKey={JSON.stringify(filters)}>{pageRows => <div className="ofp-table-scroll"><table className="ofp-delivery-table"><thead><tr>
            <th scope="col">الطلب والتواريخ</th><th scope="col">جهة التنفيذ</th><th scope="col">الدفع وجهة التحصيل</th><th scope="col">حالة التحصيل</th>
            {DELIVERY_MONEY_COLUMNS.map(column => <th scope="col" key={column.key}>{column.label}</th>)}
        </tr></thead><tbody>{pageRows.map(row => <tr key={row.id} data-testid="financial-delivery-row">
            <td><strong>{deliveryOrderLabel(row)}</strong>
                {row.recognition_event === "order_completed" && <small>تم التنفيذ: {row.completed_at ? formatRiyadhDateTime(row.completed_at) : UNAVAILABLE}</small>}
                <small>التسليم: {row.delivered_at ? formatRiyadhDateTime(row.delivered_at) : UNAVAILABLE}</small></td>
            <td><strong>{positionText(row.party_name)}</strong><small>{PARTY_LABELS[row.party_type] || UNAVAILABLE}</small>
                {row.recognition_event === "order_completed" && <><small>{shippingRecognitionLabel(row)}</small>{row.payment_mode === "prepaid" && <small>صندوق الخصم: {positionText(row.cash_account_name)}</small>}</>}</td>
            <td><strong>{paymentLabel(row.payment_method)}</strong><small>{destinationLabel(row.collection_destination)}</small>{row.destination_name && <small>{row.destination_name}</small>}</td>
            <td><CollectionStatus value={row.collection_status} /></td>
            {DELIVERY_MONEY_COLUMNS.map(column => <td key={column.key}><Money value={row[column.key]} role={column.role} /></td>)}
        </tr>)}</tbody></table></div>}</PaginatedReportTable>
        {!rows.length && <p className="ofp-empty-cell">{allRows.length ? "لا توجد تفاصيل تطابق الفلاتر المحددة." : delivery?.complete === true ? "لا توجد طلبات موثقة ضمن هذا القسم حتى آخر احتساب." : "لم تتوفر أدلة كافية لعرض تفاصيل الطلبات."}</p>}
        <Notes items={[DELIVERY_SCOPE_NOTE, ...(delivery?.notes || [])]} />
    </article>;
}

function ReportWarnings({ report, data }) {
    const dependencies = (report.dependencies || []).filter(item => !["available", "ready", "complete", "active"].includes(item.status));
    const messages = readableMessages([...(report.issues || []), ...dependencies]);
    const incomplete = ["actual", "forecast", "profit"].some(key => data.completeness?.[key] !== true);
    if (!messages.length && !incomplete) return null;
    return <aside className="ofp-warning" role="status"><WarningCircle size={21} aria-hidden="true" /><div>
        {incomplete && <strong>بعض أقسام التقرير غير مكتملة؛ المبلغ غير المتاح لا يُعرض كصفر.</strong>}
        {!incomplete && <div className="ofp-print-only"><strong>توجد ملاحظات على بيانات التقرير تحتاج إلى مراجعة.</strong></div>}
        <div className="ofp-no-print">{messages.map(message => <p key={message}>{message}</p>)}</div>
    </div></aside>;
}

export default function OperationalFinancialPosition() {
    const auth = useOptionalAuth();
    const user = auth?.user;
    const authLoading = Boolean(auth?.loading);
    const authMissing = Boolean(auth && !authLoading && !user);
    const scope = auth ? JSON.stringify([user?.id, user?.created_by, user?.owner_id, user?.merchant_id, user?.tenant_id, user?.role, user?.is_owner, auth.authStatus]) : "standalone";
    const [revision, setRevision] = useState(0);
    const [state, setState] = useState({ scope, phase: "loading", report: null, error: "" });
    const [currency, setCurrency] = useState("");
    const [filters, setFilters] = useState(emptyFilters);
    const [exporting, setExporting] = useState(false);
    const [exportError, setExportError] = useState("");
    const [printing, setPrinting] = useState(false);
    const printRef = useRef(null);
    const readLane = useRef({});

    useEffect(() => {
        setState({ scope, phase: "loading", report: null, error: "" });
        setExportError("");
        if (authLoading) return;
        if (authMissing) {
            setState({ scope, phase: "denied", report: null, error: "يلزم تسجيل الدخول لعرض التقرير المالي التشغيلي." });
            return;
        }
        return visibleOperationalRead(async config => {
            const update = await refreshVisibleSnapshot(operationalApi, config);
            return {...await operationalApi.financialPosition(config), ...(update?.refresh_warning ? {refresh_warning: update.refresh_warning} : {})};
        }, {
            onData: report => {
                if (report?.permissions?.reports !== true) {
                    setState({ scope, phase: "denied", report: null, error: "ليست لديك صلاحية عرض التقرير المالي التشغيلي." });
                } else if (!isPositionContract(report)) {
                    setState({ scope, phase: "error", report: null, error: "تعذر قراءة بيانات التقرير. أعد المحاولة." });
                } else {
                    setState({ scope, phase: "ready", report, error: "" });
                }
            },
            onError: error => {
                const status = error?.response?.status;
                const denied = status === 401 || status === 403;
                setState({ scope, phase: denied ? "denied" : "error", report: null, error: status === 403
                    ? "ليست لديك صلاحية عرض التقرير المالي التشغيلي."
                    : status === 401 ? "انتهت الجلسة؛ سجّل الدخول لعرض التقرير."
                        : error?.response?.data?.detail?.message || "تعذر تحميل التقرير الحالي. أعد المحاولة لتحديث البيانات." });
            },
        }, readLane.current);
    }, [scope, authLoading, authMissing, revision]);

    useEffect(() => { setFilters(emptyFilters()); setCurrency(""); setPrinting(false); }, [scope]);

    // Hide the old scope synchronously, before the replacement request's effect.
    const report = state.scope === scope && state.phase === "ready" ? state.report : null;
    const codes = useMemo(() => currencyCodes(report), [report]);
    const activeCurrency = codes.includes(currency) ? currency : codes.includes("SAR") ? "SAR" : codes[0] || "";
    const data = report?.status === "active" ? report.currencies[activeCurrency] : null;
    const loading = state.scope !== scope || state.phase === "loading";
    const handlePrint = useReactToPrint({
        contentRef: printRef,
        documentTitle: `المركز المالي التشغيلي${activeCurrency ? ` - ${activeCurrency}` : ""}`,
        pageStyle: "@page { size: A4 landscape; margin: 10mm; }",
        onAfterPrint: () => setPrinting(false),
        onPrintError: () => setPrinting(false),
    });
    const printReport = () => {
        flushSync(() => setPrinting(true));
        handlePrint();
    };
    const refresh = () => {
        setPrinting(false);
        setState({ scope, phase: "loading", report: null, error: "" });
        setRevision(value => value + 1);
    };
    const exportReport = async () => {
        if (!data || exporting) return;
        setExporting(true); setExportError("");
        try {
            const workbook = buildFinancialPositionWorkbook(report, activeCurrency, filters);
            await XLSX.writeFile(workbook, `operational-financial-position-${activeCurrency}-${fileStampSA()}.xlsx`);
        } catch {
            setExportError("تعذر تصدير التقرير. أعد المحاولة.");
        } finally { setExporting(false); }
    };
    const tables = data?.tables || [];

    return <main className="ofp-page" dir="rtl" aria-label="تقرير المركز المالي التشغيلي" data-testid="operational-financial-position">
        <div className="ofp-toolbar ofp-no-print"><div><span className="ofp-eyebrow">الأرصدة التشغيلية / التقارير</span><h1>المركز المالي التشغيلي</h1></div><div className="ofp-actions">
            {data && user?.role === "owner" && <a className="ofp-button" href="/operational-balances/provider-fees">مراجعة تعرفات منصات الدفع</a>}
            <button type="button" className="ofp-button" onClick={refresh} disabled={loading || authMissing}><ArrowClockwise size={19} aria-hidden="true" />تحديث التقرير</button>
            <button type="button" className="ofp-button" onClick={printReport} disabled={!data}><Printer size={20} aria-hidden="true" />طباعة التقرير</button>
            <button type="button" className="ofp-button ofp-export" onClick={exportReport} disabled={!data || exporting}><MicrosoftExcelLogo size={20} aria-hidden="true" />{exporting ? "جارٍ التصدير…" : "تصدير Excel"}</button>
        </div></div>
        {loading && <div className="ofp-page-notice" role="status">جارٍ تحميل التقرير المالي التشغيلي…</div>}
        {state.scope === scope && state.error && <div className="ofp-page-notice ofp-error" role="alert"><WarningCircle size={24} aria-hidden="true" /><p>{state.error}</p></div>}
        {exportError && <p className="ofp-warning ofp-no-print" role="alert">{exportError}</p>}
        {report && report.status !== "active" && <div className="ofp-page-notice" role="status"><Info size={24} aria-hidden="true" /><p>{inactiveMessages[report.status] || "التقرير التشغيلي الحالي غير متاح في هذه المرحلة."}</p></div>}
        {report?.status === "active" && !data && <div className="ofp-page-notice" role="status">لا توجد بيانات عملة جاهزة للعرض حتى آخر احتساب.</div>}
        {report?.refresh_warning && <p role="alert" className="ofp-warning">{report.refresh_warning}</p>}
        {data && user?.role === "owner" && <ProviderHistoryReview key={"history" + scope + report.session_scope} sessionScope={report.session_scope} />}
        {data && user?.role === "owner" && <NativePurchaseReview key={scope + report.session_scope} sessionScope={report.session_scope} onRefresh={refresh} />}
        {data && user?.role === "owner" && <CustomerWalletBalance key={"wallet" + scope + report.session_scope} sessionScope={report.session_scope} onSaved={refresh} />}
        {data && user?.role === "owner" && <div className="ofp-no-print">
            <a className="ofp-button" href="/shipping-settings-v2">مراجعة تعرفة شركات الشحن</a>
            <HistoricalShippingFeeReview key={scope + report.session_scope + activeCurrency} orders={data.shipping_fee_review_orders} />
            <CompletedShippingFeeReview key={`completed:${scope}:${report.session_scope}:${activeCurrency}`} sessionScope={report.session_scope} orders={data.shipping_fee_review_orders} />
        </div>}
        {data && <article className="ofp-report" dir="rtl" ref={printRef} data-testid="financial-position-content">
            <header className="ofp-report-header"><div className="ofp-brand">ميزان<span>التقرير التشغيلي</span></div><div><h2>تقرير المركز المالي التشغيلي</h2><p>الأرصدة والذمم كما في {formatRiyadhArabicLong(report.as_of)}</p></div><span className="ofp-report-kind">الأرصدة والتوقعات</span></header>
            <div className="ofp-scope-bar"><div><CalendarBlank size={19} aria-hidden="true" /><span>آخر احتساب: <bdi>{formatRiyadhDateTime(report.as_of)}</bdi></span></div>
                <label className="ofp-no-print">العملة<select aria-label="عملة التقرير" value={activeCurrency} onChange={event => { setCurrency(event.target.value); setFilters(emptyFilters()); }}>
                    {codes.map(code => <option key={code} value={code}>{code === "SAR" ? "ريال سعودي · SAR" : code}</option>)}
                </select></label><div className="ofp-print-only">العملة: {activeCurrency}</div>
                <div>منذ بدء التشغيل: <bdi>{formatRiyadhDateTime(report.started_at)}</bdi></div>
            </div>
            <ReportWarnings report={report} data={data} />

            <section className="ofp-section" aria-labelledby="ofp-actual-heading">
                <SectionHeading id="ofp-actual-heading" number="01" title="الأرصدة الفعلية والحسابات"><span className="ofp-positive">لنا: مستحق للمنشأة</span><span className="ofp-negative">علينا: مستحق للغير</span></SectionHeading>
                <div className="ofp-table-grid">{tables.filter(table => table.section === "actual").map(table => <ReportTable key={`${activeCurrency}:${table.id}`} table={table} printing={printing} />)}</div>
            </section>

            <section className="ofp-section" aria-labelledby="ofp-fulfillment-heading">
                <SectionHeading id="ofp-fulfillment-heading" number="02" title="الشحن والتحصيل والمبالغ المتوقعة"><span>أزرق: أرصدة فعلية</span><span className="ofp-forecast-label">أصفر: مبالغ تقديرية</span></SectionHeading>
                <div className="ofp-table-grid">{tables.filter(table => table.section === "fulfillment" || table.section === "forecast").map(table => <ReportTable key={`${activeCurrency}:${table.id}`} table={table} printing={printing} />)}</div>
                <DeliveryDetails key={activeCurrency} delivery={data.deliveries} filters={filters} onFiltersChange={setFilters} currency={activeCurrency} />
            </section>

            <section className="ofp-section" aria-labelledby="ofp-summary-heading">
                <SectionHeading id="ofp-summary-heading" number="03" title="الإجماليات والصافي والربح المتوقع"><span>العملة: {activeCurrency}</span></SectionHeading>
                <div className="ofp-summary-grid">
                    <DebtTable key={`debt:${activeCurrency}`} data={data} printing={printing} />
                    <AmountTable key={`profit:${activeCurrency}`} title="حساب الربح المتوقع" rows={data.profit_rows} className="ofp-profit" complete={data.completeness?.profit === true} printing={printing} />
                    <AmountTable key={`current:${activeCurrency}`} title="صافي الأرصدة الحالية" rows={data.current_rows} className="ofp-current" complete={data.completeness?.actual === true} printing={printing} />
                    <AmountTable key={`forecast:${activeCurrency}`} title="التغير المتوقع من الطلبات المفتوحة" rows={data.forecast_rows} className="ofp-forecast-summary" complete={data.completeness?.forecast === true} printing={printing} />
                </div>
                <div className="ofp-final-metrics">
                    <div data-testid="financial-forecast-position"><h3>صافي السيولة والمستحقات المتوقع</h3><strong><Money value={data.metrics?.forecast_monetary_position} role="receivable" /><span>{activeCurrency}</span></strong></div>
                    <div data-testid="financial-net-profit"><h3>صافي الربح المتوقع</h3><strong><Money value={data.metrics?.net_profit} role="receivable" /><span>{activeCurrency}</span></strong></div>
                </div>
            </section>
            <Notes items={data.notes} className="ofp-report-notes" />
            <footer className="ofp-report-footer"><span>الأرصدة الحالية والتوقعات معروضة بصورة منفصلة. غير متاح يعني أن الأدلة غير مكتملة.</span><span>إعداد التقرير: <bdi>{formatRiyadhDateTime(report.generated_at)}</bdi> · الرياض</span></footer>
        </article>}
    </main>;
}
