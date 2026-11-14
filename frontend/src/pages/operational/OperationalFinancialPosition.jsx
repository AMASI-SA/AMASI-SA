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
import { canReadOperationalPage, visibleOperationalRead, refreshVisibleSnapshot } from "./visibleOperationalRead";
import { FINANCIAL_PAGES, financialPage, financialViewFromSearch, financialViewHref,
    financialSnapshotKey, financialSnapshotParams, hasFinancialSnapshot, sameFinancialSnapshot } from "./financialPositionViews";

const emptyFilters = () => ({ order: "", party: "", payment: "" });
const inactiveMessages = {
    draft: "يظهر التقرير المالي التشغيلي بعد اعتماد الأرصدة الافتتاحية وبدء التشغيل.",
    frozen: "التشغيل مجمّد حاليًا؛ التقرير الحالي غير متاح.",
    accounting_active: "بدأ النظام المحاسبي؛ التقرير التشغيلي الحالي غير متاح ضمن هذه المرحلة.",
};

// Every user intent is bounded, including time waiting for an aborted physical
// request to finish. Only explicit manual actions can grant a POST refresh.
const REPORT_LOAD_TIMEOUT_MS = 150000;
const SECTION_READ_TIMEOUT_MS = 65000;

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

function ReportTable({ table, printing, confirmationControl }) {
    const columns = table.columns || [];
    return <article className={`ofp-table-card${["providers", "ads_currency_conversion", "customer_wallets"].includes(table.id) ? " ofp-table-wide" : ""}${table.kind === "forecast" ? " ofp-table-forecast" : ""}`} data-testid={`financial-table-${table.id}`}>
        <div className="ofp-table-title"><h3>{table.title}</h3>{table.complete !== true && <span className="ofp-status">غير مكتمل</span>}</div>
        <PaginatedReportTable rows={table.rows || []} label={table.title} pageSize={table.id === "provider_collections_expected" ? 10 : 13} showAll={printing}>{rows => <div className="ofp-table-scroll"><table>
            <thead><tr>{columns.map(column => <th scope="col" key={column.key}>{column.label}</th>)}</tr></thead>
            <tbody>{rows.map((row, index) => <tr key={row.id || index}>{columns.map(column =>
                <td key={column.key} className={column.type === "text" ? "ofp-label-cell" : "ofp-value-cell"}>{column.type === "exceptions" && !printing
                    ? <PendingOrdersDialog count={row[column.key]} details={row.pending_order_details} name={row.name || table.title} confirmationControl={confirmationControl} />
                    : <DataCell value={row[column.key]} column={column} />}</td>)}</tr>)}
                {!table.rows?.length && <tr><td className="ofp-empty-cell" colSpan={Math.max(columns.length, 1)}>{table.complete === true ? "لا توجد أرصدة مسجلة لهذا القسم." : "لم تتوفر بيانات مكتملة لهذا القسم."}</td></tr>}
            </tbody>
            {table.show_totals !== false && table.totals && Object.keys(table.totals).length > 0 && <tfoot><tr>{columns.map((column, index) => <td key={column.key}>
                {index === 0 && column.type === "text" ? "الإجمالي" : <DataCell value={table.totals[column.key]} column={column} />}
            </td>)}</tr></tfoot>}
        </table></div>}</PaginatedReportTable>
        {table.unassigned_order_details?.length > 0 && <div className="ofp-no-print"><PendingOrdersDialog count={table.unassigned_order_details.length} details={table.unassigned_order_details} name="طلبات لم تثبت جهة توصيلها" confirmationControl={confirmationControl} /></div>}
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
                <small>التسليم: {row.delivered_at ? formatRiyadhDateTime(row.delivered_at) : UNAVAILABLE}</small>
                {["delivered_order_created_after_start", "owner_confirmed_after_start"].includes(row.recognition_basis) && <small>وقت التسجيل: {row.recorded_at ? formatRiyadhDateTime(row.recorded_at) : UNAVAILABLE}</small>}</td>
            <td><strong>{positionText(row.party_name)}</strong><small>{PARTY_LABELS[row.party_type] || UNAVAILABLE}</small>
                {(row.recognition_event === "order_completed" || ["delivered_order_created_after_start", "owner_confirmed_after_start"].includes(row.recognition_basis)) && <small>{shippingRecognitionLabel(row)}</small>}
                {row.recognition_event === "order_completed" && row.payment_mode === "prepaid" && <small>صندوق الخصم: {positionText(row.cash_account_name)}</small>}</td>
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
    const incomplete = (report.forecast_pending ? ["actual", "profit"] : ["actual", "forecast", "profit"])
        .some(key => data.completeness?.[key] !== true);
    if (!messages.length && !incomplete) return null;
    return <aside className="ofp-warning" role="status"><WarningCircle size={21} aria-hidden="true" /><div>
        {incomplete && <strong>بعض أقسام التقرير غير مكتملة؛ المبلغ غير المتاح لا يُعرض كصفر.</strong>}
        {!incomplete && <div className="ofp-print-only"><strong>توجد ملاحظات على بيانات التقرير تحتاج إلى مراجعة.</strong></div>}
        <div className="ofp-no-print">{messages.map(message => <p key={message}>{message}</p>)}</div>
    </div></aside>;
}

function ForecastPending({ message, retry, loading }) {
    return <article className="ofp-table-card ofp-forecast-summary" data-testid="financial-forecast-pending">
        <div className="ofp-table-title"><h3>التغير المتوقع من الطلبات المفتوحة</h3></div>
        <div className="ofp-forecast-pending" role="status" aria-busy={loading}>
            <p>{message}</p>
            {!loading && <button type="button" className="ofp-button ofp-no-print" onClick={retry}>إعادة قراءة الملخص والتوقعات</button>}
        </div>
    </article>;
}

export default function OperationalFinancialPosition() {
    const auth = useOptionalAuth();
    const user = auth?.user;
    const authLoading = Boolean(auth?.loading);
    const authMissing = Boolean(auth && !authLoading && !user);
    const scope = auth ? JSON.stringify([user?.id, user?.created_by, user?.owner_id, user?.merchant_id, user?.tenant_id, user?.role, user?.is_owner, auth.authStatus]) : "standalone";
    const [view, setView] = useState(() => financialViewFromSearch(window.location.search));
    const [intent, setIntent] = useState({ id: 0, kind: "read" });
    const [state, setState] = useState({ scope, view, phase: "loading", report: null, error: "" });
    const [forecast, setForecast] = useState({ key: "", phase: "idle", error: "" });
    const [currency, setCurrency] = useState("");
    const [filters, setFilters] = useState(emptyFilters);
    const [exporting, setExporting] = useState(false);
    const [exportError, setExportError] = useState("");
    const [printing, setPrinting] = useState(false);
    const [deliveryConfirmations, setDeliveryConfirmations] = useState(null);
    const printRef = useRef(null), readLane = useRef({}), refreshLock = useRef(false);
    const consumedRefreshIntent = useRef(-1);
    const exportCleanup = useRef(null), exportActivityCleanup = useRef(null), exportGeneration = useRef(0), forecastAttempt = useRef(null);
    const latestReport = useRef(null), confirmedDeliveries = useRef(null), confirmationRefresh = useRef(null);

    useEffect(() => {
        const pop = () => {
            exportGeneration.current += 1; exportCleanup.current?.(); exportActivityCleanup.current?.();
            setView(financialViewFromSearch(window.location.search)); setIntent(previous => ({ id: previous.id + 1, kind: "read" }));
        };
        window.addEventListener("popstate", pop);
        return () => window.removeEventListener("popstate", pop);
    }, []);

    useEffect(() => {
        const followUp = confirmationRefresh.current;
        confirmationRefresh.current = null;
        const manual = intent.kind === "refresh" && intent.scope === scope && intent.view === view
            && consumedRefreshIntent.current < intent.id;
        // Effect restarts during auth checks or scope changes cannot reuse a
        // user action. Consuming this grant also applies if no request can run.
        if (intent.kind === "refresh") consumedRefreshIntent.current = Math.max(consumedRefreshIntent.current, intent.id);
        refreshLock.current = manual;
        setForecast(previous => previous.phase === "loading" ? { ...previous, phase: "paused",
            error: "توقفت قراءة التوقعات عند إعادة التحقق من الصفحة. أعد قراءة الملخص لاستكمالها." } : previous);
        setState(previous => ({ scope, view, phase: manual ? "refreshing" : "loading", error: "",
            report: previous.scope === scope && previous.view === view ? previous.report : null }));
        setExportError("");
        if (authLoading) return;
        if (authMissing) {
            setState({ scope, view, phase: "denied", report: null, error: "يلزم تسجيل الدخول لعرض التقرير المالي التشغيلي." });
            return;
        }
        const read = async config => {
            const update = manual ? await refreshVisibleSnapshot(operationalApi, config) : null;
            const report = await operationalApi.financialPosition({ ...config, params: { view } });
            if (followUp?.scope === scope && (report?.session_scope !== followUp.sessionScope
                || Date.parse(report?.started_at) !== Date.parse(followUp.periodStart))) {
                const error = new Error("تغيرت جلسة التشغيل؛ أعد قراءة التقرير من الجلسة الحالية.");
                error.response = { status: 409, data: { detail: { message: error.message } } };
                throw error;
            }
            return { ...report, ...(update?.refresh_warning ? { refresh_warning: update.refresh_warning } : {}) };
        };
        const callbacks = {
            onData: report => {
                if (report?.permissions?.reports !== true) {
                    setState({ scope, view, phase: "denied", report: null, error: "ليست لديك صلاحية عرض التقرير المالي التشغيلي." });
                } else if (!isPositionContract(report) || report.view !== view || !Number.isSafeInteger(report.revision)) {
                    setState({ scope, view, phase: "error", report: null, error: "تعذر التحقق من بيانات هذه الصفحة. أعد قراءتها." });
                } else {
                    setState({ scope, view, phase: "ready", report, error: "", attempt: intent.id });
                }
            },
            onError: error => {
                const status = error?.response?.status;
                const denied = status === 401 || status === 403;
                const timedOut = error?.code === "OPERATIONAL_VISIBLE_READ_TIMEOUT";
                setState(previous => ({ scope, view, phase: denied ? "denied" : timedOut ? "timed_out" : "error",
                    report: !denied && previous.scope === scope && previous.view === view ? previous.report : null,
                    error: timedOut ? "انتهت مهلة هذه المحاولة؛ تبقى آخر لقطة محفوظة بتاريخها إن كانت متاحة. أعد القراءة عند الحاجة."
                        : status === 403 ? "ليست لديك صلاحية عرض التقرير المالي التشغيلي."
                        : status === 401 ? "انتهت الجلسة؛ سجّل الدخول لعرض التقرير."
                        : error?.response?.data?.detail?.message || "تعذر إكمال الطلب؛ تبقى آخر لقطة محفوظة بتاريخها إن كانت متاحة." }));
            },
            onPaused: () => setState(previous => ({ scope, view, phase: "paused",
                report: previous.scope === scope && previous.view === view ? previous.report : null,
                error: "توقفت المحاولة عند مغادرة الصفحة أو انقطاع الاتصال. أعد القراءة عند الحاجة." })),
            onEnd: () => { refreshLock.current = false; },
        };
        const cleanup = visibleOperationalRead(read, callbacks, readLane.current, {
            singleAttemptTimeoutMs: manual ? REPORT_LOAD_TIMEOUT_MS : SECTION_READ_TIMEOUT_MS,
            // An explicit refresh/confirmation grant is consumed on hide, never
            // deferred until the operator returns to this page.
            deferUntilVisible: !manual,
        });
        return () => { cleanup(); refreshLock.current = false; };
    }, [scope, authLoading, authMissing, view, intent]);

    useEffect(() => { setFilters(emptyFilters()); setCurrency(""); setPrinting(false); }, [scope]);
    useEffect(() => { setFilters(emptyFilters()); setPrinting(false); }, [view]);

    // Clear the old page/scope synchronously, before effect cleanup or a late response.
    const report = !authLoading && !authMissing && state.scope === scope && state.view === view && state.phase !== "denied" ? state.report : null;
    latestReport.current = { scope, view, report, authLoading, authMissing, attempt: intent.id };
    const snapshotKey = financialSnapshotKey(report);
    const codes = useMemo(() => currencyCodes(report), [report]);
    const activeCurrency = codes.includes(currency) ? currency : codes.includes("SAR") ? "SAR" : codes[0] || "";
    const data = report?.status === "active" ? report.currencies[activeCurrency] : null;
    const loading = state.scope !== scope || state.view !== view || state.phase === "loading"
        || (state.phase === "ready" && state.attempt !== intent.id);
    const refreshing = state.scope === scope && state.view === view && state.phase === "refreshing";
    const page = financialPage(view);
    const fullExporting = exporting === "all";

    useEffect(() => {
        exportGeneration.current += 1;
        exportCleanup.current?.(); exportCleanup.current = null;
        exportActivityCleanup.current?.();
        setExporting(false); setExportError("");
        return () => { exportGeneration.current += 1; exportCleanup.current?.(); exportActivityCleanup.current?.(); };
    }, [scope, authLoading, authMissing, view, intent.id, snapshotKey]);

    useEffect(() => {
        if (authLoading || authMissing || view !== "summary" || state.phase !== "ready" || state.attempt !== intent.id || report?.status !== "active"
            || report.forecast_pending !== true || fullExporting) return;
        const key = JSON.stringify([scope, intent.id, snapshotKey]);
        if (forecastAttempt.current === key) return;
        forecastAttempt.current = key;
        if (!hasFinancialSnapshot(report)) {
            setForecast({ key, phase: "error", error: "التوقعات غير متاحة قبل حفظ أول احتساب مؤرخ." });
            return;
        }
        const expected = report;
        setForecast({ key, phase: "loading", error: "" });
        return visibleOperationalRead(config => operationalApi.financialPosition({ ...config,
            params: { view: "forecast", ...financialSnapshotParams(expected) } }), {
            onData: received => {
                if (latestReport.current?.authLoading || latestReport.current?.authMissing) return;
                if (received?.permissions?.reports !== true) {
                    setState({ scope, view, phase: "denied", report: null, error: "ليست لديك صلاحية عرض التقرير المالي التشغيلي." });
                    return;
                }
                if (!isPositionContract(received) || received.view !== "forecast" || received.forecast_pending !== false
                    || !sameFinancialSnapshot(expected, received)) {
                    setForecast({ key, phase: "error", error: "لم تطابق التوقعات لقطة الملخص؛ أعد قراءة الصفحة لعرض لقطة واحدة متسقة." });
                    return;
                }
                setState(previous => previous.scope === scope && previous.view === view
                    && sameFinancialSnapshot(expected, previous.report)
                    ? { ...previous, report: { ...received,
                        ...(previous.report.refresh_warning ? { refresh_warning: previous.report.refresh_warning } : {}) } } : previous);
                setForecast({ key, phase: "ready", error: "" });
            },
            onError: error => {
                const status = error?.response?.status;
                if (status === 401 || status === 403) {
                    setState({ scope, view, phase: "denied", report: null, error: "انتهت صلاحية قراءة التقرير؛ أعد تسجيل الدخول." });
                    return;
                }
                setForecast({ key, phase: "error", error: status === 409
                    ? "تغيرت لقطة الأرصدة؛ أعد قراءة الصفحة قبل عرض التوقعات معها."
                    : "تعذرت قراءة التوقعات؛ تبقى الأرصدة والربح من اللقطة المحفوظة ظاهرة." });
            },
            onPaused: () => setForecast({ key, phase: "paused", error: "توقفت قراءة التوقعات عند مغادرة الصفحة. أعد قراءتها عند الحاجة." }),
        }, readLane.current, { singleAttemptTimeoutMs: SECTION_READ_TIMEOUT_MS, deferUntilVisible: false });
    }, [scope, authLoading, authMissing, view, state.phase, state.attempt, intent.id, snapshotKey, fullExporting]);

    const navigate = (event, nextView) => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (nextView === view) return;
        exportGeneration.current += 1; exportCleanup.current?.(); exportActivityCleanup.current?.();
        confirmationRefresh.current = null;
        window.history.pushState(window.history.state, "", financialViewHref(nextView));
        setView(nextView);
        setIntent(previous => ({ id: previous.id + 1, kind: "read" }));
    };
    const reload = () => {
        exportGeneration.current += 1; exportCleanup.current?.(); exportActivityCleanup.current?.();
        confirmationRefresh.current = null;
        setIntent(previous => ({ id: previous.id + 1, kind: "read" }));
    };
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
        if (refreshLock.current || authMissing || authLoading || !canReadOperationalPage()) return;
        exportGeneration.current += 1; exportCleanup.current?.(); exportActivityCleanup.current?.();
        refreshLock.current = true;
        setPrinting(false);
        setState(previous => ({ ...previous, phase: "refreshing", error: "" }));
        setIntent(previous => ({ id: previous.id + 1, kind: "refresh", scope, view }));
    };
    const deliverySaved = (record, { refreshAllowed } = {}) => {
        const latest = latestReport.current;
        if (latest.scope !== scope || latest.report?.permissions?.confirm_delivery_after_start !== true
            || latest.report?.session_scope !== record.session_scope) return;
        const key = JSON.stringify([record.order_id, record.order_number, record.party_type, record.party_id]);
        const previous = confirmedDeliveries.current;
        const records = previous?.scope === scope && previous.sessionScope === record.session_scope
            && previous.periodStart === latest.report.started_at ? previous.records : {};
        if (records[key]?.id === record.id) return;
        const saved = { scope, sessionScope: record.session_scope, periodStart: latest.report.started_at,
            records: { ...records, [key]: record }, latest: record };
        confirmedDeliveries.current = saved;
        setDeliveryConfirmations(saved);
        if (refreshAllowed && canReadOperationalPage()) {
            confirmationRefresh.current = { scope, sessionScope: record.session_scope, periodStart: latest.report.started_at };
            refresh();
        }
    };
    const confirmationControl = { allowed: report?.permissions?.confirm_delivery_after_start === true,
        sessionScope: report?.session_scope, periodStart: report?.started_at, onSaved: deliverySaved,
        recordedConfirmations: deliveryConfirmations?.scope === scope && deliveryConfirmations.sessionScope === report?.session_scope
            && deliveryConfirmations.periodStart === report?.started_at
            ? deliveryConfirmations.records : {} };
    const confirmationNotice = useMemo(() => {
        const saved = deliveryConfirmations;
        if (!saved || saved.scope !== scope || state.phase === "denied"
            || (report && (saved.sessionScope !== report.session_scope || saved.periodStart !== report.started_at))) return "";
        let status = saved.latest.state;
        const matches = (row, proof) => row.order_id === saved.latest.order_id && row.order_number === saved.latest.order_number
            && proof?.state === "applied" && proof.can_confirm === false && proof.confirmation_id === saved.latest.id
            && proof.party_type === saved.latest.party_type && proof.party_id === saved.latest.party_id
            && Date.parse(proof.confirmed_at) === Date.parse(saved.latest.confirmed_at);
        for (const currency of Object.values(report?.currencies || {})) {
            if ((currency.deliveries?.rows || []).some(row => row.recognition_basis === "owner_confirmed_after_start"
                && row.party_type === saved.latest.party_type && row.party_id === saved.latest.party_id
                && Date.parse(row.recorded_at) === Date.parse(saved.latest.confirmed_at)
                && matches(row, row.delivery_after_start_confirmation))) status = "applied";
            for (const table of currency.tables || []) {
                for (const row of [...(table.rows || []), { pending_order_details: table.unassigned_order_details || [] }]) {
                    if ((row.pending_order_details || []).some(item => matches(item, item.delivery_after_start_confirmation))) status = "applied";
                }
            }
        }
        if (status === "applied") return "تم حفظ التأكيد وتطبيقه في التقرير.";
        return "تم حفظ التأكيد؛ بانتظار تحديث الأرصدة";
    }, [deliveryConfirmations, report, scope, state.phase]);
    const watchExportActivity = generation => {
        exportActivityCleanup.current?.();
        const cleanup = () => {
            document.removeEventListener("visibilitychange", activity);
            window.removeEventListener("offline", activity);
            if (exportActivityCleanup.current === cleanup) exportActivityCleanup.current = null;
        };
        const activity = () => {
            if (canReadOperationalPage()) return;
            cleanup();
            if (exportGeneration.current !== generation) return;
            // A hidden/offline export stays revoked even if serialization only
            // finishes after the operator returns to the page.
            exportGeneration.current += 1;
            exportCleanup.current?.();
            setExporting(false); setExportError("أُلغي التصدير عند مغادرة الصفحة أو انقطاع الاتصال.");
        };
        exportActivityCleanup.current = cleanup;
        document.addEventListener("visibilitychange", activity);
        window.addEventListener("offline", activity);
        return cleanup;
    };
    const exportReport = async () => {
        if (!data || exporting || authLoading || authMissing || !canReadOperationalPage()) return;
        const generation = ++exportGeneration.current;
        const expected = report, attempt = intent.id;
        const current = () => exportGeneration.current === generation && canReadOperationalPage()
            && !latestReport.current?.authLoading && !latestReport.current?.authMissing
            && latestReport.current?.scope === scope && latestReport.current?.view === view
            && latestReport.current?.attempt === attempt
            && financialSnapshotKey(expected) === financialSnapshotKey(latestReport.current?.report);
        setExporting("page"); setExportError("");
        const finishActivity = watchExportActivity(generation);
        try {
            const workbook = buildFinancialPositionWorkbook(report, activeCurrency, filters, { view, title: page.label });
            const downloaded = await XLSX.writeFile(workbook, `operational-financial-position-${view}-${activeCurrency}-${fileStampSA()}.xlsx`, { shouldContinue: current });
            if (downloaded === false && exportGeneration.current === generation) setExportError("أُلغي التصدير عند مغادرة الصفحة أو تغير بياناتها.");
        } catch {
            if (exportGeneration.current === generation) setExportError("تعذر تصدير هذه الصفحة. أعد المحاولة.");
        } finally { finishActivity(); if (exportGeneration.current === generation) setExporting(false); }
    };
    const exportAll = () => {
        if (!data || exporting || loading || refreshing || !hasFinancialSnapshot(report) || !canReadOperationalPage()) return;
        const expected = report, selectedCurrency = activeCurrency, attempt = intent.id;
        const generation = ++exportGeneration.current;
        const current = () => exportGeneration.current === generation && canReadOperationalPage()
            && !latestReport.current?.authLoading && !latestReport.current?.authMissing
            && latestReport.current?.scope === scope && latestReport.current?.view === view
            && latestReport.current?.attempt === attempt && sameFinancialSnapshot(expected, latestReport.current?.report);
        setExporting("all"); setExportError("");
        const finishActivity = watchExportActivity(generation);
        if (report.forecast_pending) setForecast({ key: JSON.stringify([scope, intent.id, snapshotKey]), phase: "paused",
            error: "الأرصدة المحفوظة ظاهرة؛ أعد قراءة الصفحة لاستكمال التوقعات بعد التصدير." });
        exportCleanup.current?.();
        exportCleanup.current = visibleOperationalRead(config => operationalApi.financialPosition({ ...config,
            params: { view: "all", ...financialSnapshotParams(expected) } }), {
            onData: received => {
                if (!current()) { finishActivity(); return; }
                if (received?.permissions?.reports !== true) {
                    setState({ scope, view, phase: "denied", report: null, error: "ليست لديك صلاحية عرض التقرير المالي التشغيلي." });
                    finishActivity(); setExporting(false); return;
                }
                if (!isPositionContract(received)
                    || received.view !== "all" || !sameFinancialSnapshot(expected, received)) {
                    setExportError("لم يطابق التقرير الكامل الجلسة واللقطة المعروضة؛ أعد قراءة الصفحة قبل التصدير.");
                    finishActivity(); setExporting(false); return;
                }
                try {
                    const workbook = buildFinancialPositionWorkbook(received, selectedCurrency, {}, { view: "all", title: "جميع أقسام التقرير" });
                    if (!current()) { finishActivity(); return; }
                    Promise.resolve(XLSX.writeFile(workbook, `operational-financial-position-all-${selectedCurrency}-${fileStampSA()}.xlsx`, { shouldContinue: current }))
                        .then(downloaded => { if (downloaded === false && exportGeneration.current === generation) setExportError("أُلغي التصدير عند مغادرة الصفحة أو تغير بياناتها."); })
                        .catch(() => { if (current()) setExportError("تعذر تصدير التقرير الكامل. أعد المحاولة."); })
                        .finally(() => { finishActivity(); if (exportGeneration.current === generation) setExporting(false); });
                } catch {
                    finishActivity();
                    if (current()) { setExportError("تعذر تصدير التقرير الكامل. أعد المحاولة."); setExporting(false); }
                }
            },
            onError: error => {
                finishActivity();
                if (!current()) return;
                if ([401, 403].includes(error?.response?.status)) {
                    setState({ scope, view, phase: "denied", report: null, error: "انتهت صلاحية قراءة التقرير؛ أعد تسجيل الدخول." });
                    setExporting(false); return;
                }
                setExportError(error?.response?.status === 409 ? "تغيرت لقطة الأرصدة؛ أعد قراءة الصفحة قبل تصدير التقرير الكامل."
                    : "تعذر تحميل التقرير الكامل للتصدير. لم يُنشأ ملف جزئي.");
                setExporting(false);
            },
            onPaused: () => { finishActivity(); if (exportGeneration.current === generation) { setExporting(false); setExportError("أُلغي التصدير عند مغادرة الصفحة."); } },
        }, readLane.current, { singleAttemptTimeoutMs: SECTION_READ_TIMEOUT_MS, deferUntilVisible: false });
    };
    const tables = data?.tables || [];

    const forecastState = forecast.key === JSON.stringify([scope, intent.id, snapshotKey]) ? forecast : { phase: "loading", error: "" };
    const pendingForecast = report?.forecast_pending === true;
    const forecastMessage = forecastState.error || (refreshing ? "تبقى توقعات اللقطة السابقة غير مكتملة أثناء تحديث المصادر." : "جارٍ قراءة توقعات نفس لقطة الأرصدة؛ يمكنك متابعة الملخص الحالي.");

    return <main className="ofp-page" dir="rtl" aria-label="تقرير المركز المالي التشغيلي" data-testid="operational-financial-position">
        <div className="ofp-toolbar ofp-no-print"><div><span className="ofp-eyebrow">الأرصدة التشغيلية / التقارير</span><h1>المركز المالي التشغيلي</h1><p className="ofp-navigation-note">تقرأ الصفحات آخر احتساب محفوظ. تحديث المصادر إجراء واحد مشترك لكل الأقسام.</p></div><div className="ofp-actions">
            <button type="button" className="ofp-button" onClick={refresh} disabled={loading || refreshing || authMissing || authLoading}><ArrowClockwise size={19} aria-hidden="true" />{refreshing ? "جارٍ تحديث المصادر…" : "تحديث المصادر والأرصدة"}</button>
            <button type="button" className="ofp-button" onClick={printReport} disabled={!data}><Printer size={20} aria-hidden="true" />طباعة هذه الصفحة</button>
            <button type="button" className="ofp-button ofp-export" onClick={exportReport} disabled={!data || Boolean(exporting)}><MicrosoftExcelLogo size={20} aria-hidden="true" />{exporting === "page" ? "جارٍ التصدير…" : "تصدير هذه الصفحة"}</button>
            <button type="button" className="ofp-button" onClick={exportAll} disabled={!data || Boolean(exporting) || loading || refreshing || !hasFinancialSnapshot(report)}>{fullExporting ? "جارٍ تحميل التقرير الكامل…" : `تصدير جميع الأقسام${activeCurrency ? ` · ${activeCurrency}` : ""}`}</button>
        </div></div>
        <div className="ofp-layout">
            <aside className="ofp-sidebar ofp-no-print"><h2>صفحات المركز المالي</h2><nav aria-label="صفحات المركز المالي">{FINANCIAL_PAGES.map(item =>
                <a key={item.id} href={financialViewHref(item.id)} aria-current={view === item.id ? "page" : undefined}
                    onClick={event => navigate(event, item.id)}>{item.label}</a>)}</nav></aside>
            <div className="ofp-page-content" aria-busy={loading && !data}>
                <div className="ofp-page-title ofp-no-print"><h2>{page.label}</h2><p>{page.description}</p></div>
                {loading && <div className="ofp-page-notice" role="status">جارٍ قراءة الصفحة من آخر لقطة محفوظة…</div>}
                {refreshing && <div className="ofp-refresh-notice ofp-no-print" role="status">جارٍ تحديث المصادر لجميع الأقسام. تبقى آخر لقطة معروضة بتاريخ احتسابها حتى اكتمال التحديث.</div>}
                {state.scope === scope && state.view === view && state.error && <div className="ofp-page-notice ofp-error" role="alert"><WarningCircle size={24} aria-hidden="true" /><div><p>{state.error}</p><button className="ofp-button ofp-no-print" type="button" onClick={reload} disabled={authMissing}>إعادة قراءة الصفحة</button></div></div>}
                {exportError && <p className="ofp-warning ofp-no-print" role="alert">{exportError}</p>}
                {report && report.status !== "active" && <div className="ofp-page-notice" role="status"><Info size={24} aria-hidden="true" /><p>{inactiveMessages[report.status] || "التقرير التشغيلي الحالي غير متاح في هذه المرحلة."}</p></div>}
                {report?.status === "active" && !data && <div className="ofp-page-notice" role="status">لا توجد بيانات عملة جاهزة للعرض حتى آخر احتساب.</div>}
                {report?.status === "active" && !data && view === "summary" && pendingForecast && <ForecastPending message={forecastMessage} loading={forecastState.phase === "loading"} retry={reload} />}
                {report?.refresh_warning && <p role="alert" className="ofp-warning">{report.refresh_warning}</p>}
                {confirmationNotice && <p role="status" className="ofp-warning ofp-no-print">{confirmationNotice}</p>}
                {data && user?.role === "owner" && view === "providers" && <div className="ofp-owner-controls ofp-no-print">
                    <a className="ofp-button" href="/operational-balances/provider-fees">مراجعة تعرفات منصات الدفع</a>
                    <ProviderHistoryReview key={"history" + scope + report.session_scope} sessionScope={report.session_scope} />
                </div>}
                {data && user?.role === "owner" && view === "suppliers" && <NativePurchaseReview key={scope + report.session_scope} sessionScope={report.session_scope} onRefresh={refresh} />}
                {data && user?.role === "owner" && view === "other" && <CustomerWalletBalance key={"wallet" + scope + report.session_scope} sessionScope={report.session_scope} onSaved={refresh} />}
                {data && user?.role === "owner" && ["couriers", "drivers"].includes(view) && <div className="ofp-owner-controls ofp-no-print">
                    {view === "couriers" && <a className="ofp-button" href="/shipping-settings-v2">مراجعة تعرفة شركات الشحن</a>}
                    <HistoricalShippingFeeReview key={scope + report.session_scope + activeCurrency} orders={data.shipping_fee_review_orders} />
                    <CompletedShippingFeeReview key={`completed:${scope}:${report.session_scope}:${activeCurrency}`} sessionScope={report.session_scope} orders={data.shipping_fee_review_orders} />
                </div>}
                {data && <article className="ofp-report" dir="rtl" ref={printRef} data-testid="financial-position-content">
                    <header className="ofp-report-header"><div className="ofp-brand">ميزان<span>التقرير التشغيلي</span></div><div><h2>{page.label}</h2><p>الأرصدة والذمم كما في {formatRiyadhArabicLong(report.as_of)}</p></div><span className="ofp-report-kind">{view === "summary" ? "الإجماليات الستة" : "صفحة من التقرير المالي"}</span></header>
                    <div className="ofp-scope-bar"><div><CalendarBlank size={19} aria-hidden="true" /><span>آخر احتساب محفوظ: <bdi>{formatRiyadhDateTime(report.as_of)}</bdi></span></div>
                        <label className="ofp-no-print">العملة<select aria-label="عملة التقرير" value={activeCurrency} onChange={event => { setCurrency(event.target.value); setFilters(emptyFilters()); }}>
                            {codes.map(code => <option key={code} value={code}>{code === "SAR" ? "ريال سعودي · SAR" : code}</option>)}
                        </select></label><div className="ofp-print-only">العملة: {activeCurrency}</div>
                        <div>منذ بدء التشغيل: <bdi>{formatRiyadhDateTime(report.started_at)}</bdi></div>
                    </div>
                    <ReportWarnings report={report} data={data} />
                    {view !== "summary" && <section className="ofp-section" aria-labelledby="ofp-page-heading">
                        <SectionHeading id="ofp-page-heading" number="01" title={page.label}><span className="ofp-positive">لنا: مستحق للمنشأة</span><span className="ofp-negative">علينا: مستحق للغير</span></SectionHeading>
                        <div className="ofp-table-grid">{tables.map(table => <ReportTable key={`${activeCurrency}:${table.id}`} table={table} printing={printing} confirmationControl={confirmationControl} />)}</div>
                        {data.deliveries && ["couriers", "drivers", "expected"].includes(view) && <DeliveryDetails key={activeCurrency} delivery={data.deliveries} filters={filters} onFiltersChange={setFilters} currency={activeCurrency} />}
                    </section>}
                    {view === "summary" && <section className="ofp-section" aria-labelledby="ofp-summary-heading">
                        <SectionHeading id="ofp-summary-heading" number="01" title="الإجماليات والصافي والربح المتوقع"><span>العملة: {activeCurrency}</span></SectionHeading>
                        <div className="ofp-summary-grid">
                            <DebtTable key={`debt:${activeCurrency}`} data={data} printing={printing} />
                            <AmountTable key={`profit:${activeCurrency}`} title="حساب الربح المتوقع" rows={data.profit_rows} className="ofp-profit" complete={data.completeness?.profit === true} printing={printing} />
                            <AmountTable key={`current:${activeCurrency}`} title="صافي الأرصدة الحالية" rows={data.current_rows} className="ofp-current" complete={data.completeness?.actual === true} printing={printing} />
                            {pendingForecast ? <ForecastPending message={forecastMessage} loading={forecastState.phase === "loading"} retry={reload} />
                                : <AmountTable key={`forecast:${activeCurrency}`} title="التغير المتوقع من الطلبات المفتوحة" rows={data.forecast_rows} className="ofp-forecast-summary" complete={data.completeness?.forecast === true} printing={printing} />}
                        </div>
                        <div className="ofp-final-metrics">
                            <div data-testid="financial-forecast-position"><h3>صافي السيولة والمستحقات المتوقع</h3>{pendingForecast ? <p role="status">{forecastMessage}</p> : <strong><Money value={data.metrics?.forecast_monetary_position} role="receivable" /><span>{activeCurrency}</span></strong>}</div>
                            <div data-testid="financial-net-profit"><h3>صافي الربح المتوقع</h3><strong><Money value={data.metrics?.net_profit} role="receivable" /><span>{activeCurrency}</span></strong></div>
                        </div>
                    </section>}
                    <Notes items={data.notes} className="ofp-report-notes" />
                    <footer className="ofp-report-footer"><span>نطاق العرض والطباعة: {page.label} · {activeCurrency}. غير متاح يعني أن الأدلة غير مكتملة.</span><span>إعداد التقرير: <bdi>{formatRiyadhDateTime(report.generated_at)}</bdi> · الرياض</span></footer>
                </article>}
            </div>
        </div>
    </main>;
}
