import { useEffect, useRef, useState } from "react";
import { useOptionalAuth } from "../../context/AuthContext";
import { grantedWebLink, managedWebPages } from "../../lib/webPageAccess";
import { formatRiyadhDateTime } from "../../lib/dates";
import { operationalApi as api, requestId } from "./api";
import PaginatedReportTable from "./PaginatedReportTable";
import { canReadOperationalPage, visibleOperationalRead } from "./visibleOperationalRead";
import {
    MAX_STATEMENT_FILE_BYTES, STATEMENT_COUNTS, STATEMENT_KINDS, STATEMENT_PROVIDERS,
    STATEMENT_AVERAGE_REASONS, STATEMENT_RECORD_EFFECTS, STATEMENT_STATUSES,
    isSavedStatementFiles, isStatementAverage, isStatementHistory, isStatementReview, savedStatementUnavailableReason,
    statementActionHref, statementConfirmationAction, statementErrorMessage,
    statementEstimatedAmount, statementMoney, statementRate, statementRowMessage, statementText,
} from "./providerStatementPresentation";
import "./operational.css";
import "./providerStatements.css";
import OperationalToolBack from './OperationalToolBack';
import { ResourceIcon } from './ApprovedFinanceUI';

const providerNames = { ...STATEMENT_PROVIDERS, emkan: "إمكان", paypal: "باي بال" };
const statementPath = provider => `/operational-balances/provider-statements${provider ? `?provider=${encodeURIComponent(provider)}` : ""}`;

const date = value => typeof value === "string" && value && Number.isFinite(Date.parse(value)) ? formatRiyadhDateTime(value) : "غير متاح";
const accepts = row => Boolean(statementConfirmationAction(row));
const createsEvent = row => ["record_missing_capture", "record_missing_refund"].includes(statementConfirmationAction(row));
const intake = kind => kind === "upload" || kind === "saved";
const uncertain = kind => kind === "upload"
    ? "لم نتأكد من نتيجة الرفع. يمكنك إعادة نفس المحاولة؛ لن يُنشأ كشف مكرر."
    : kind === "saved" ? "لم نتأكد من نتيجة استخدام الكشف. أعد نفس المحاولة أو اقرأ السجل للتحقق؛ لن تُكرر العمليات."
    : "لم نتأكد من نتيجة الاعتماد. أعد نفس المحاولة أو اقرأ المطابقة مجددًا للتحقق من السجل.";
const snapshot = result => ({ session_scope: result.session_scope, active: result.active, revision: result.revision,
    started_at: result.started_at, as_of: result.as_of, permissions: result.permissions });

function ProposedEffect({ row }) {
    if (statementConfirmationAction(row) === "link_native_refund") return <div className="op-statement-effect">
        <strong>ربط مرجع الاسترجاع الوارد بالاسترجاع المحتسب دون خصم إضافي</strong>
        <p>المرجع الوارد: <bdi>{row.native_alias.reference}</bdi> · <bdi>{statementMoney(row.native_alias.amount)} {row.native_alias.native_currency}</bdi></p>
        <p>{row.native_alias.source === "canonical_salla" ? "سجل سلة المعتمد" : "سجل الاسترجاعات"} · {date(row.native_alias.event_date)}</p>
        <small>اختر الاسترجاع المحتسب المقابل لربط المرجعين بعملية واحدة.</small>
    </div>;
    if (!createsEvent(row)) return null;
    const effect = row.proposed_effect;
    return <div className="op-statement-effect">
        <strong>{effect.employee_case_id ? "سيُستكمل رد المبلغ على سجل الموظف القائم"
            : effect.kind === "provider_refund" ? "سيُسجل رد مبلغ غير مسجل" : "ستُسجل دفعة عميل غير مسجلة"}</strong>
        {effect.employee_case_id && <p>السجل الحالي دليل لم يُحتسب ماليًا. اختره صراحةً لاعتماد أثره مرة واحدة مع الاحتفاظ بسجل الموظف.</p>}
        <p>قيمة العملية: <bdi>{statementMoney(effect.principal_amount)} {effect.native_currency}</bdi> لدى {STATEMENT_PROVIDERS[effect.provider]}.</p>
        <p>ذمة المنصة من أصل المدفوع: <bdi>{statementMoney(effect.remaining_before)}</bdi> قبل العملية، <bdi>{statementMoney(effect.remaining_after)}</bdi> بعدها.</p>
        <small>قبل العمولة والتسويات البنكية المستقلة. لا يُنشأ تحويل إلى البنك.</small>
    </div>;
}

function AverageSummary({ value, provider, currency, compact = false }) {
    if (!isStatementAverage(value, provider, currency)) {
        const reason = (value?.available === false && STATEMENT_AVERAGE_REASONS[value.reason_code]) || STATEMENT_AVERAGE_REASONS.source_average_unavailable;
        return compact ? <><small>متوسط تقديري: غير متاح</small><small>{reason}</small></>
            : <section className="op-statement-average" aria-label="متوسط رسوم المنصة"><h3>متوسط العمولة المتغيرة قبل الضريبة</h3><p>{reason}</p>
                <p className="op-muted">تظل الرسوم الفعلية المكتملة في الكشف قابلة للمطابقة؛ غياب المتوسط لا يحولها إلى تقديرات.</p></section>;
    }
    if (compact) return <><small>متوسط تقديري: <bdi dir="ltr">{statementRate(value.rate_percent)}%</bdi> · {value.eligible_sale_rows} عملية بيع</small>
        <small>قبل الضريبة والرسم الثابت · مصدره: <bdi>{statementText(value.statement_reference)}</bdi></small></>;
    const basis = { statement_date: "تاريخ الكشف", sale_event_date: "تاريخ البيع في الكشف", accepted_at: "وقت اعتماد الكشف" };
    const vatKnown = value.vat_rate_status === "explicit_uniform" && statementRate(value.vat_rate_percent) !== "غير متاح";
    return <section className="op-statement-average" aria-label="متوسط رسوم المنصة">
        <h3>متوسط العمولة المتغيرة قبل الضريبة · {STATEMENT_PROVIDERS[provider]}</h3>
        <strong className="op-statement-rate"><bdi dir="ltr">{statementRate(value.rate_percent)}%</bdi></strong>
        <p>تقديري · مجموع عمولات البيع المتغيرة قبل الضريبة مقسومًا على أصل المبيعات الصالحة في المصدر.</p>
        <dl><div><dt>إجمالي أصل المبيعات</dt><dd><bdi>{statementMoney(value.principal_total)} {currency}</bdi></dd></div>
            <div><dt>العمولة المتغيرة قبل الضريبة</dt><dd><bdi>{statementMoney(value.fee_variable_net_total)} {currency}</bdi></dd></div>
            <div><dt>الرسوم الثابتة في المصدر</dt><dd><bdi>{statementMoney(value.fee_fixed_net_total)} {currency}</bdi></dd></div>
            <div><dt>عمليات البيع الداخلة في المتوسط</dt><dd>{value.eligible_sale_rows}</dd></div></dl>
        <dl><div><dt>رسم ثابت تقديري لكل عملية</dt><dd><bdi>{statementEstimatedAmount(value.fixed_amount_per_sale)} {currency}</bdi></dd></div>
            <div><dt>نسبة الضريبة المثبتة</dt><dd>{vatKnown ? <bdi dir="ltr">{statementRate(value.vat_rate_percent)}%</bdi> : "غير متاحة"}</dd></div>
            <div><dt>ضريبة رسوم مبيعات المصدر</dt><dd><bdi>{statementMoney(value.fee_vat_total)} {currency}</bdi></dd></div>
            <div><dt>عمولة التحويل</dt><dd>تُراجع مستقلة على سطور الكشف</dd></div></dl>
        <p>مصدر المتوسط: <bdi>{statementText(value.statement_reference)}</bdi> · {basis[value.source_date_basis] || "تاريخ المصدر"}: {date(value.source_as_of)}</p>
        {!vatKnown && <p className="op-muted">{value.vat_rate_status === "source_conflict"
            ? "نسبة الضريبة الواردة تختلف عن مبالغ المصدر؛ تقدير التكلفة الكاملة يحتاج مراجعتها."
            : "نسبة الضريبة غير متاحة بصورة موحدة؛ تقدير التكلفة الكاملة يحتاج مراجعتها."}</p>}
        <p className="op-muted">يُستخدم المتوسط كتقدير عند غياب رسوم مؤكدة للعملية. تظل رسوم العمليات المطابقة أدناه فعلية من الكشف.</p>
    </section>;
}

function Frame({ children, providerId }) {
    const pages = managedWebPages(useOptionalAuth()?.user);
    return <main className="op-page op-statements mz-finance-tool" dir="rtl" data-testid="provider-statements-page">
        <OperationalToolBack/><header><span className="op-eyebrow">ميزان 2 · الأرصدة التشغيلية</span><h1>مطابقة كشوف منصات الدفع{providerNames[providerId] ? ` · ${providerNames[providerId]}` : ""}</h1>
            <p>ارفع كشف تابي أو تمارا أو سلة. نتعرف على المنصة تلقائيًا ونطابق عمليات الكشف مع سجلات الدفع والرد المحفوظة.</p>
            {grantedWebLink(pages, "/operational-balances/financial-position?view=providers") && <a className="op-statement-link" href="/operational-balances/financial-position?view=providers">عرض أرصدة منصات الدفع</a>}
        </header>{children}
    </main>;
}

export default function ProviderStatements({ providerId: selectedProvider } = {}) {
    const providerId = selectedProvider ?? new URLSearchParams(window.location.search).get("provider") ?? "";
    const supported = !providerId || Object.hasOwn(STATEMENT_PROVIDERS, providerId);
    const auth = useOptionalAuth();
    const user = auth?.user;
    const owner = user?.is_owner === true || String(user?.role || "").toLowerCase() === "owner";
    const grant = managedWebPages(user)?.find(page => page.id === "balances.statements" && ["read", "write"].includes(page.mode));
    const identity = JSON.stringify([user?.web_page_access, user?.mobile_app_access, user?.permissions,
        user?.id, user?.created_by, user?.owner_id, user?.merchant_id, user?.tenant_id, user?.role, user?.is_owner, auth?.authStatus]);
    if (auth?.loading || (auth?.authStatus && auth.authStatus !== "authenticated")) return <Frame providerId={providerId}><p role="status">جارٍ التحقق من الجلسة…</p></Frame>;
    if (!user || (!owner && !grant)) return <Frame providerId={providerId}><p role="alert" className="op-error">تحتاج صلاحية كشوف منصات الدفع لعرض هذه الصفحة.</p></Frame>;
    if (!supported) return <Frame providerId={providerId}><section className="op-statement-upload" role="status">
        <h2>{providerNames[providerId] ? `كشوف ${providerNames[providerId]}` : "منصة غير معروفة"}</h2>
        <p>الاستيراد الآلي المتاح هنا لكشوف تابي وتمارا وسلة فقط. لا يوجد مستورد معتمد لهذه المنصة في هذه الصفحة.</p>
        <a className="op-statement-link" href={statementPath()}>عرض الكشوف المدعومة</a>
    </section></Frame>;
    return <Frame providerId={providerId}><StatementWorkspace key={JSON.stringify([identity, providerId])} providerId={providerId} writable={owner || grant?.mode === "write"} /></Frame>;
}

function StatementWorkspace({ writable, providerId }) {
    const inProvider = item => !providerId || item.provider === providerId;
    const pages = managedWebPages(useOptionalAuth()?.user);
    const ledgerPath = `/operational-balances?view=ledger&kind=provider&id=${encodeURIComponent(providerId)}`;
    const focusSection = useRef(window.location.hash.slice(1));
    function revealAnchor(node) {
        if (node && node.id === focusSection.current) {
            focusSection.current = '';
            node.focus({ preventScroll: true });
            node.scrollIntoView?.({ block: 'start' });
        }
    }
    const [history, setHistory] = useState(null), [detail, setDetail] = useState(null);
    const [savedFilesOpen, setSavedFilesOpen] = useState(false), [savedFiles, setSavedFiles] = useState(null);
    const [selectedSavedId, setSelectedSavedId] = useState("");
    const [file, setFile] = useState(null), [fileKey, setFileKey] = useState(0);
    const [choices, setChoices] = useState({}), [acknowledged, setAcknowledged] = useState(false);
    const [confirmedUnrecorded, setConfirmedUnrecorded] = useState(false);
    const [busy, setBusy] = useState("read"), [pending, setPending] = useState(null);
    const [error, setError] = useState(""), [notice, setNotice] = useState(""), [denied, setDenied] = useState(false);
    const [readable, setReadable] = useState(canReadOperationalPage);
    const alive = useRef(true), readGeneration = useRef(0), readCleanup = useRef(null), readLane = useRef({});
    const context = useRef(null), currentDetail = useRef(null), writeLock = useRef(false), operation = useRef(null), pendingCommand = useRef(null);

    function clearSelection() { setChoices({}); setAcknowledged(false); setConfirmedUnrecorded(false); }
    function clearPending() { pendingCommand.current = null; setPending(null); }
    function resetSession() {
        context.current = null; currentDetail.current = null;
        setHistory(null); setDetail(null); setFile(null); setFileKey(value => value + 1);
        setSavedFiles(null); setSavedFilesOpen(false); setSelectedSavedId("");
        clearSelection(); clearPending(); setNotice("");
    }
    function acceptReview(result, expectedScope, expectedId, expectedStart, saved = false) {
        if (!isStatementReview(result) || result.statement.id !== expectedId) throw new Error("invalid_statement_review");
        if (result.session_scope !== expectedScope || result.started_at !== expectedStart) {
            resetSession();
            const failure = new Error("statement_session_changed");
            failure.response = { status: 409 }; throw failure;
        }
        context.current = snapshot(result); currentDetail.current = result;
        setDetail(result); clearSelection(); setSelectedSavedId("");
        setHistory(previous => {
            const items = previous?.session_scope === result.session_scope ? previous.items : [];
            const cached = items.find(row => row.id === result.statement.id);
            const item = { ...cached, ...result.statement,
                counts: saved ? result.counts : cached?.counts ?? null,
                reviewed_at: saved ? result.reviewed_at || result.statement.reviewed_at || null : cached?.reviewed_at,
                review_as_of: saved ? result.as_of : cached?.review_as_of,
                statement_average: saved ? result.statement_average : cached?.statement_average };
            return { ...snapshot(result), has_more: previous?.has_more === true, items: [item, ...items.filter(row => row.id !== item.id)] };
        });
    }

    function read(kind, id, initial = false) {
        if (writeLock.current || denied) return;
        if (kind === "saved" && context.current?.permissions.use_saved_file !== true) return;
        if (kind !== "history" && !context.current) return;
        const expectedScope = context.current?.session_scope;
        const expectedStart = context.current?.started_at;
        const generation = ++readGeneration.current;
        readCleanup.current?.();
        setBusy(kind === "saved" ? "saved_read" : "read"); setError(""); setNotice("");
        if (kind === "saved") { setSavedFiles(null); setSelectedSavedId(""); }
        if (kind === "review") { currentDetail.current = null; setDetail(null); clearSelection(); }
        const current = () => alive.current && generation === readGeneration.current;
        readCleanup.current = visibleOperationalRead(config => kind === "history"
            ? api.providerStatements(config) : kind === "saved" ? api.providerStatementSavedFiles(config) : api.providerStatementReview(id, config), {
            onData: result => {
                if (!current()) return;
                if (kind === "history") {
                    if (!isStatementHistory(result)) throw new Error("invalid_statement_history");
                    if (context.current && (context.current.session_scope !== result.session_scope || context.current.started_at !== result.started_at)) {
                        setFile(null); setFileKey(value => value + 1);
                    }
                    context.current = snapshot(result); currentDetail.current = null;
                    setHistory(result); setDetail(null); clearSelection();
                    setSavedFiles(null); setSelectedSavedId("");
                } else if (kind === "saved") {
                    if (!isSavedStatementFiles(result)) throw new Error("invalid_saved_statement_files");
                    if (result.session_scope !== expectedScope || result.started_at !== expectedStart) {
                        resetSession(); const failure = new Error("statement_session_changed");
                        failure.response = { status: 409 }; throw failure;
                    }
                    setSavedFiles(result); setSelectedSavedId("");
                } else acceptReview(result, expectedScope, id, expectedStart);
                // Saved-file metadata cannot establish whether an uncertain
                // use command committed; retain its exact identity for retry.
                if (kind !== "saved") clearPending();
            },
            onError: failure => {
                if (!current()) return;
                if ([401, 403].includes(failure?.response?.status)) { resetSession(); setDenied(true); }
                setError(statementErrorMessage(failure, "تعذرت قراءة الكشوف. أعد القراءة عند الحاجة."));
            },
            onPaused: () => { if (current()) setError("توقفت القراءة عند مغادرة الصفحة أو انقطاع الاتصال. أعد القراءة عند العودة."); },
            onEnd: () => { if (current()) setBusy(""); },
        }, readLane.current, { singleAttemptTimeoutMs: 35000, deferUntilVisible: initial });
    }

    useEffect(() => {
        alive.current = true;
        read("history", null, true);
        const activity = () => {
            const available = canReadOperationalPage(); setReadable(available);
            const current = operation.current;
            if (!available && current?.valid) {
                current.valid = false;
                current.controller.abort();
                setError(uncertain(current.command.kind));
            }
        };
        document.addEventListener("visibilitychange", activity);
        window.addEventListener("offline", activity);
        window.addEventListener("online", activity);
        return () => {
            alive.current = false; readGeneration.current += 1; readCleanup.current?.();
            if (operation.current) { operation.current.valid = false; operation.current.controller.abort(); }
            document.removeEventListener("visibilitychange", activity);
            window.removeEventListener("offline", activity);
            window.removeEventListener("online", activity);
        };
        // Each authenticated owner gets a separate mounted workspace. Reads are
        // explicit after this first visible history request, never effect retries.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    async function send(command) {
        if (!writable || !command || writeLock.current || !alive.current || !canReadOperationalPage() || denied) return;
        const current = context.current;
        if (command.kind === "saved" && current?.permissions.use_saved_file !== true) return;
        if (!current?.active || command.body.expected_session_scope !== current.session_scope
            || current.permissions[intake(command.kind) ? "upload" : "confirm"] !== true) return;
        if (intake(command.kind) && (!current.started_at || command.body.expected_started_at !== current.started_at)) return;
        writeLock.current = true; readGeneration.current += 1; readCleanup.current?.();
        const attempt = { command, controller: new AbortController(), valid: true };
        operation.current = attempt; pendingCommand.current = command; setPending(command);
        setBusy(command.kind); setError(""); setNotice("");
        const stillCurrent = () => alive.current && attempt.valid && operation.current === attempt;
        try {
            const config = { signal: attempt.controller.signal };
            const result = command.kind === "upload"
                ? await api.uploadProviderStatement(command.file, command.body, config)
                : command.kind === "saved" ? await api.useSavedProviderStatement(command.id, command.body, config)
                : await api.confirmProviderStatement(command.id, command.body, config);
            if (!stillCurrent()) return;
            if (command.kind === "confirm" && (!Array.isArray(result?.applied_row_ids)
                || result.applied_row_ids.length !== command.body.decisions.length
                || new Set(result.applied_row_ids).size !== result.applied_row_ids.length
                || !command.body.decisions.every(decision => result.applied_row_ids.includes(decision.row_id)))) {
                throw new Error("unconfirmed_statement_result");
            }
            acceptReview(result, command.body.expected_session_scope, intake(command.kind) ? result?.statement?.id : command.id,
                intake(command.kind) ? command.body.expected_started_at : command.startedAt, true);
            clearPending(); setFile(null); setFileKey(value => value + 1);
            setNotice(intake(command.kind)
                ? result.replayed || result.duplicate ? "هذا الكشف محفوظ مسبقًا. عُرضت مطابقته دون تكرار العمليات."
                    : command.kind === "saved" ? "استُخدم الملف الأصلي المحفوظ وعُرضت مطابقته. راجع العمليات التي تحتاج قرارًا أدناه."
                        : "حُفظ الكشف وعُرضت المطابقة. راجع العمليات التي تحتاج قرارًا أدناه."
                : result.replayed ? "هذا الاعتماد مسجل مسبقًا. لم تُكرر العمليات."
                    : command.body.confirmed_unrecorded ? "حُفظ الاعتماد وسُجلت العمليات المالية المحددة. تم تحديث دليل العمولة."
                        : command.body.decisions.every(decision => decision.action === "link_native_refund") ? "حُفظ ربط مرجع الاسترجاع بالعملية المحتسبة دون خصم إضافي."
                        : "حُفظ اعتماد العمليات المختارة. تم تحديث دليل العمولة.");
        } catch (failure) {
            if (!stillCurrent()) return;
            const status = failure?.response?.status;
            if ([401, 403].includes(status)) { resetSession(); setDenied(true); }
            // Rejected input has no accepted command. Transport failures and
            // conflicts keep the exact request for explicit retry or fresh review.
            else if ([400, 413, 415, 422].includes(status)) clearPending();
            setError(statementErrorMessage(failure, uncertain(command.kind)));
        } finally {
            if (operation.current === attempt) { operation.current = null; writeLock.current = false; }
            if (alive.current) setBusy("");
        }
    }

    function chooseFile(event) {
        if (busy || pendingCommand.current) return;
        const selected = event.target.files?.[0];
        setFile(null); setError(""); setNotice(""); setSelectedSavedId("");
        currentDetail.current = null; setDetail(null); clearSelection();
        if (!selected) return;
        if (!/\.xlsx$/i.test(selected.name)) { setError("اختر ملف Excel أصليًا بصيغة .xlsx من تابي أو تمارا أو سلة."); return; }
        if (!selected.size || selected.size > MAX_STATEMENT_FILE_BYTES) { setError("اختر ملفًا غير فارغ لا يتجاوز 10 ميجابايت."); return; }
        setFile(selected);
    }
    function upload(event) {
        event.preventDefault();
        if (!file || !writable || busy || pendingCommand.current || !context.current?.permissions.upload) return;
        send({ kind: "upload", file, body: { request_id: requestId(), expected_session_scope: context.current.session_scope, expected_started_at: context.current.started_at } });
    }
    function toggleSavedFiles() {
        if (context.current?.permissions.use_saved_file !== true) return;
        if (savedFilesOpen) {
            if (busy === "saved_read") { readGeneration.current += 1; readCleanup.current?.(); setBusy(""); }
            setSavedFilesOpen(false); setSavedFiles(null); setSelectedSavedId("");
        } else {
            setSavedFilesOpen(true); read("saved");
        }
    }
    function chooseSavedFile(id) {
        if (!canUseSaved || !savedFiles.items.some(item => item.id === id && item.can_import && inProvider(item))) return;
        setSelectedSavedId(id); setFile(null); setFileKey(value => value + 1); setError(""); setNotice("");
        currentDetail.current = null; setDetail(null); clearSelection();
    }
    function useSavedFile(event) {
        event.preventDefault();
        if (!canUseSaved) return;
        const selected = savedFiles.items.find(item => item.id === selectedSavedId && item.can_import && item.original_available && inProvider(item));
        if (!selected) return;
        send({ kind: "saved", id: selected.id, body: { request_id: requestId(), expected_session_scope: savedFiles.session_scope,
            expected_started_at: savedFiles.started_at, expected_file_hash: selected.file_hash } });
    }
    function confirm(event) {
        event.preventDefault();
        const reviewed = currentDetail.current;
        if (!writable || busy || pendingCommand.current || !reviewed || !inProvider(reviewed.statement) || !acknowledged || !reviewed.permissions.confirm) return;
        const selected = reviewed.rows.filter(row => Object.hasOwn(choices, row.id));
        if (!selected.length || selected.some(row => !accepts(row)
            || (row.existing.length && !row.existing.some(record => record.id === choices[row.id])))) return;
        const recordsNewEvents = selected.some(createsEvent);
        if (recordsNewEvents && !confirmedUnrecorded) return;
        send({ kind: "confirm", id: reviewed.statement.id, startedAt: reviewed.started_at, body: { request_id: requestId(), expected_session_scope: reviewed.session_scope,
            expected_preview_hash: reviewed.preview_hash, decisions: selected.map(row => ({ row_id: row.id, action: statementConfirmationAction(row),
                ...(row.existing.length ? { existing_id: choices[row.id] } : {}) })), confirmed: true,
            ...(recordsNewEvents ? { confirmed_unrecorded: true } : {}) } });
    }

    const selected = detail?.rows.filter(row => Object.hasOwn(choices, row.id)) || [];
    const completeSelection = selected.length > 0 && selected.every(row => accepts(row)
        && (!row.existing.length || row.existing.some(record => record.id === choices[row.id])));
    const recordsNewEvents = selected.some(createsEvent);
    const disabled = Boolean(busy || pending || !readable || !history?.active || denied);
    const canConfirm = writable && detail && inProvider(detail.statement) && detail.active && detail?.permissions.confirm && history?.session_scope === detail?.session_scope;
    const historyItems = history?.items.filter(inProvider) || [];
    const savedItems = savedFiles?.items.filter(inProvider) || [];
    const canUseSaved = writable && !disabled && history?.permissions.use_saved_file === true && history?.permissions.upload
        && savedFiles?.active && savedFiles.permissions.upload && savedFiles.permissions.use_saved_file === true
        && Boolean(savedFiles.started_at) && savedFiles.session_scope === history?.session_scope && savedFiles.started_at === history?.started_at;
    return <>
        {providerId && <div className="op-statement-toolbar" aria-label="سياق المنصة">
            <strong>كشوف {providerNames[providerId]}</strong><a className="op-statement-link" href={statementPath()}>كل المنصات</a>
            {grantedWebLink(pages, ledgerPath) && <a className="op-statement-link" href={ledgerPath}>العودة إلى حساب {providerNames[providerId]}</a>}
            <p>يُكتشف نوع الكشف من الملف نفسه. اختيار الحساب لا يغيّر منصة العمليات.</p>
        </div>}
        {error && <p role="alert" className="op-error">{error}</p>}
        {notice && <p role="status" className="op-success">{notice}</p>}
        {denied ? <p className="op-muted">تعذّر الوصول إلى كشوف المنصات ضمن صلاحيات هذا الحساب.</p> : <>
            <div className="op-statement-toolbar">
                <button type="button" onClick={() => read("history")} disabled={Boolean(busy) || !readable}>قراءة سجل الكشوف</button>
                {busy && <span role="status">{busy === "upload" ? "جارٍ رفع الكشف ومطابقته…" : busy === "saved" ? "جارٍ استخدام الكشف المحفوظ ومطابقته…" : busy === "confirm" ? "جارٍ حفظ الاعتماد…" : "جارٍ قراءة الكشوف…"}</span>}
            </div>
            {pending && !busy && <section className="op-statement-pending" aria-label="محاولة تحتاج تحقق">
                <p>احتُفظ بنفس المحاولة لحماية السجل من التكرار. لا تبدأ عملية أخرى قبل التحقق من النتيجة.</p>
                <button type="button" onClick={() => send(pendingCommand.current)} disabled={Boolean(busy) || !readable}>
                    {pending.kind === "upload" ? "إعادة نفس محاولة الرفع" : pending.kind === "saved" ? "إعادة نفس محاولة استخدام الكشف" : "إعادة نفس محاولة الاعتماد"}</button>
                {pending.kind === "confirm" && <button type="button" onClick={() => read("review", pending.id)} disabled={Boolean(busy) || !readable}>قراءة المطابقة مجددًا</button>}
            </section>}
            {history && <>
                {!history.active && <p className="op-error">التشغيل غير نشط. يمكنك قراءة الكشوف المحفوظة، ويتاح الرفع والاعتماد أثناء التشغيل.</p>}
                {writable && history.permissions.use_saved_file === true && <button type="button" className="op-statement-saved-toggle" aria-expanded={savedFilesOpen} onClick={toggleSavedFiles}
                    disabled={(Boolean(busy) && busy !== "saved_read") || Boolean(pending) || !readable}>
                    {savedFilesOpen ? "إغلاق قائمة الكشوف السابقة" : "استخدام كشف مرفوع سابقًا"}</button>}
                {savedFilesOpen && <section className="op-statement-saved" aria-label="الكشوف المرفوعة سابقًا">
                    <h2>الكشوف المرفوعة سابقًا</h2><p>اختر ملفًا أصليًا محفوظًا من تابي أو تمارا أو سلة، ثم استخدمه لمطابقة العمليات والرسوم داخل الأرصدة التشغيلية.</p>
                    <button type="button" onClick={() => read("saved")} disabled={Boolean(busy) || !readable}>إعادة قراءة قائمة الكشوف السابقة</button>
                    {!savedFiles ? <p className="op-muted">{busy === "saved_read" ? "جارٍ قراءة بيانات الملفات المحفوظة…" : "لم تتوفر قائمة ملفات للعرض؛ أعد قراءتها عند الحاجة."}</p>
                        : <form onSubmit={useSavedFile} data-testid="provider-saved-file-use">
                            {savedFiles.has_more && <p className="op-muted">تعرض القائمة أحدث {savedFiles.items.length} ملفًا. توجد ملفات أقدم محفوظة.</p>}
                            {!savedItems.length ? <p className="op-muted">لا توجد كشوف منصات محفوظة متاحة في هذه القائمة.</p>
                                : <PaginatedReportTable rows={savedItems} label="الكشوف السابقة المتاحة" resetKey={`${savedFiles.session_scope}:${savedFiles.started_at}`}>
                                    {rows => <div className="op-table"><table><thead><tr><th>اختيار</th><th>المنصة والكشف</th><th>وقت الرفع</th><th>حالة الأصل</th></tr></thead>
                                        <tbody>{rows.map(item => <tr key={item.id} data-testid={`saved-provider-file-${item.id}`}>
                                            <td><label><input type="radio" name="saved-provider-file" aria-label={`اختيار الكشف المحفوظ ${item.filename || item.statement_reference || item.id}`}
                                                checked={selectedSavedId === item.id} disabled={!canUseSaved || !item.can_import}
                                                onChange={() => chooseSavedFile(item.id)} /> اختيار</label></td>
                                            <td><strong><ResourceIcon resource={{kind:"provider",id:item.provider}} size="sm"/> {STATEMENT_PROVIDERS[item.provider]}</strong><span>{statementText(item.filename)}</span><small>{statementText(item.statement_reference)}</small></td>
                                            <td>{date(item.uploaded_at)}</td><td>{item.can_import ? "الملف الأصلي متاح للمطابقة" : savedStatementUnavailableReason(item)}</td>
                                        </tr>)}</tbody></table></div>}
                                </PaginatedReportTable>}
                            <p className="op-muted">تُعتمد رسوم العمليات المطابقة، وتبقى الفروق والعمليات غير المسجلة للمراجعة.</p>
                            <button type="submit" className="op-primary" disabled={!canUseSaved || !selectedSavedId}>استخدام الكشف المختار ومطابقته</button>
                        </form>}
                </section>}
                {writable && <form id="upload" tabIndex={-1} ref={revealAnchor} onSubmit={upload} className="op-statement-upload" data-testid="provider-statement-upload">
                    <h2>رفع كشف{providerId ? ` ${providerNames[providerId]}` : ""} للمطابقة</h2>
                    <label className="op-field">ملف كشف المنصة
                        <input key={fileKey} type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            aria-label="ملف كشف المنصة" disabled={disabled || !history.permissions.upload || !history.started_at} onChange={chooseFile} />
                    </label>
                    <p className="op-muted">ملف Excel أصلي · حتى 10 ميجابايت و10,000 سطر. تُعتمد رسوم العمليات المطابقة من الكشف، وتبقى الفروق للمراجعة.</p>
                    <button type="submit" className="op-primary" disabled={disabled || !history.permissions.upload || !history.started_at || !file}>رفع الكشف ومطابقته</button>
                    <p className="op-muted">رفع الكشف أو اعتماد مطابقته لا يثبت وصول تحويل إلى البنك.</p>
                </form>}
                {detail && <section className="op-statement-review" aria-label="مطابقة الكشف" data-testid="provider-statement-review">
                    {!inProvider(detail.statement) && <p role="alert" className="op-error">الملف الذي تمت قراءته يخص {providerNames[detail.statement.provider]}، وليس {providerNames[providerId]}. عُرضت هوية الملف كما وردت في المصدر؛ الاعتماد غير متاح ضمن حساب المنصة المختارة. <a href={`${statementPath(detail.statement.provider)}#history`}>افتح سجل {providerNames[detail.statement.provider]} لمراجعة الكشف</a></p>}
                    <header><h2><ResourceIcon resource={{kind:"provider",id:detail.statement.provider}}/> {STATEMENT_PROVIDERS[detail.statement.provider]} · مطابقة الكشف</h2>
                        <p>المرجع: <bdi>{statementText(detail.statement.statement_reference)}</bdi> · العملة: <bdi>{statementText(detail.statement.native_currency)}</bdi></p>
                        <p className="op-muted">{statementText(detail.statement.filename)} · مصادر الأرصدة كما في: {date(detail.as_of)}</p>
                        <p className="op-muted">دليل رسوم الكشف كما في: {date(detail.statement_evidence_as_of)}</p>
                    </header>
                    <div className="op-summary">{STATEMENT_COUNTS.map(([key, label]) => <article key={key}><span>{label}</span><strong>{detail.counts[key]}</strong></article>)}</div>
                    <AverageSummary value={detail.statement_average} provider={detail.statement.provider} currency={detail.statement.native_currency} />
                    <form onSubmit={confirm}>
                        <PaginatedReportTable rows={detail.rows} label="عمليات كشف المنصة" resetKey={`${detail.statement.id}:${detail.preview_hash}`}>
                            {rows => <div className="op-table"><table><thead><tr>
                                <th>المراجعة</th><th>الطلب والعملية</th><th>مبلغ العملية</th><th>الرسوم الفعلية</th><th>السجل المقابل</th><th>نتيجة المطابقة</th>
                            </tr></thead><tbody>{rows.map(row => {
                                const chosen = Object.hasOwn(choices, row.id), action = statementActionHref(row.action_url);
                                return <tr key={row.id} data-testid={`statement-row-${row.id}`}>
                                    <td>{accepts(row) ? <label><input type="checkbox" aria-label={`اختيار العملية ${row.order_number || row.id}`}
                                        checked={chosen} disabled={disabled || !canConfirm} onChange={event => {
                                            setChoices(previous => { const next = { ...previous }; if (event.target.checked) next[row.id] = ""; else delete next[row.id]; return next; });
                                            setAcknowledged(false); setConfirmedUnrecorded(false);
                                        }} /> اختيار</label> : <span>—</span>}</td>
                                    <td><strong><bdi>{statementText(row.order_number)}</bdi></strong><span>{STATEMENT_KINDS[row.kind]}</span><small>{date(row.event_date)}</small>
                                        <small>{row.provider === "tabby" ? "خطة الدفع الواردة بالكشف" : "نوع الدفع الوارد بالكشف"}: <bdi>{typeof row.plan_id === "string" && row.plan_id.trim() ? row.plan_id : "غير مذكورة في الكشف"}</bdi></small>
                                        {row.provider === "tamara" && <small>عدد الدفعات في الكشف: <bdi>{Number.isSafeInteger(row.installments) && row.installments > 0 ? row.installments : "غير مذكور في الكشف"}</bdi></small>}
                                        {typeof row.payment_method === "string" && row.payment_method && <small>طريقة الدفع: <bdi>{row.payment_method}</bdi></small>}
                                        {(Number.isSafeInteger(row.source_row) && row.source_row > 0) && <small>سطر الكشف: <bdi>{row.source_row}</bdi></small>}
                                    </td>
                                    <td><bdi>{statementMoney(row.principal_amount)}</bdi><small>{statementText(row.native_currency)}</small></td>
                                    <td><span>المتغير قبل الضريبة <bdi>{statementMoney(row.fee_variable_net)}</bdi></span>
                                        <span>الثابت قبل الضريبة <bdi>{statementMoney(row.fee_fixed_net)}</bdi></span>
                                        <span>مجموع الرسم <bdi>{statementMoney(row.fee_net)}</bdi></span><span>الضريبة <bdi>{statementMoney(row.fee_vat)}</bdi></span>
                                        <strong>الإجمالي <bdi>{statementMoney(row.fee_total)}</bdi></strong><small>{statementText(row.native_currency)}</small></td>
                                    <td>{row.existing.length ? <>
                                        <ul className="op-statement-records">{row.existing.map(record => <li key={record.id}>
                                            <span>{record.kind === "customer_return" ? "دليل رد عميل" : STATEMENT_KINDS[record.kind] || "سجل محفوظ"} · <bdi>{statementMoney(record.amount)}</bdi></span>
                                            <small>{statementText(record.reference)} · {STATEMENT_RECORD_EFFECTS[record.financial_effect] || "حالة الأثر تحتاج مراجعة"}</small>
                                        </li>)}</ul>
                                        {chosen && accepts(row) && <label className="op-field">السجل الذي طابقته
                                            <select aria-label={`السجل المقابل للعملية ${row.order_number || row.id}`} value={choices[row.id]} disabled={disabled || !canConfirm}
                                                onChange={event => { setChoices(previous => ({ ...previous, [row.id]: event.target.value })); setAcknowledged(false); setConfirmedUnrecorded(false); }}>
                                                <option value="">اختر السجل صراحةً</option>{row.existing.map(record => <option key={record.id} value={record.id}>
                                                    {statementText(record.reference)} · {statementMoney(record.amount)} {statementText(row.native_currency)}</option>)}
                                            </select></label>}
                                    </> : <span className="op-muted">لا يوجد سجل مقابل</span>}</td>
                                    <td><strong className={`op-statement-status op-statement-${row.status}`}>{STATEMENT_STATUSES[row.status]}</strong>
                                        <p>{statementRowMessage(row)}</p><ProposedEffect row={row} />
                                        {action && grantedWebLink(pages,action) && <a className="op-statement-link" href={action}>{row.action_label || "فتح صفحة المعالجة"}</a>}</td>
                                </tr>;
                            })}</tbody></table></div>}
                        </PaginatedReportTable>
                        {detail.rows.some(accepts) && <fieldset className="op-statement-confirm" disabled={disabled || !canConfirm}>
                            <p>العمليات المختارة: <bdi>{selected.length}</bdi>. اختر السجل المقابل لكل عملية يظهر لها سجل محفوظ.</p>
                            <label><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} aria-label="تأكيد مراجعة العمليات المختارة" />
                                راجعت العمليات المختارة والسجلات المقابلة، وأوافق على اعتماد المطابقة والرسوم الفعلية الموضحة.</label>
                            {recordsNewEvents && <label><input type="checkbox" checked={confirmedUnrecorded} onChange={event => setConfirmedUnrecorded(event.target.checked)} aria-label="تأكيد أن العمليات الجديدة غير مسجلة سابقًا" />
                                تحققت أن الدفعات أو المبالغ المردودة الجديدة المختارة غير مسجلة سابقًا، وغير داخلة في الأرصدة الافتتاحية أو أي سداد سابق. أوافق على تسجيلها وتغيير ذمة المنصة بالمبالغ الموضحة.</label>}
                            <button type="submit" className="op-primary" disabled={!acknowledged || !completeSelection || (recordsNewEvents && !confirmedUnrecorded)}>اعتماد العمليات المختارة</button>
                        </fieldset>}
                    </form>
                    <button type="button" onClick={() => read("review", detail.statement.id)} disabled={Boolean(busy) || !readable}>إعادة قراءة مطابقة هذا الكشف</button>
                </section>}
                <section id="history" tabIndex={-1} ref={revealAnchor} className="op-statement-history" aria-label="الكشوف المحفوظة"><h2>الكشوف المحفوظة</h2>
                    {history.has_more === true && <p className="op-muted">يعرض السجل أحدث {history.items.length} كشفًا. توجد كشوف أقدم محفوظة.</p>}
                    {!historyItems.length ? <p className="op-muted">لا توجد كشوف مطابقة للمنصة المختارة ضمن السجل المعروض.</p> : <PaginatedReportTable rows={historyItems} label="الكشوف المحفوظة" resetKey={history.session_scope}>
                        {rows => <div className="op-table"><table><thead><tr><th>المنصة والكشف</th><th>وقت الرفع</th><th>العمليات</th><th>آخر نتيجة مطابقة</th><th>فتح</th></tr></thead>
                            <tbody>{rows.map(item => <tr key={item.id}><td><strong><ResourceIcon resource={{kind:"provider",id:item.provider}} size="sm"/> {STATEMENT_PROVIDERS[item.provider]}</strong><span>{statementText(item.statement_reference)}</span><small>{statementText(item.filename)}</small>
                                <AverageSummary compact value={item.statement_average} provider={item.provider} currency={item.native_currency} /></td>
                                <td>{date(item.uploaded_at)}</td><td>{item.row_count} · <bdi>{statementText(item.native_currency)}</bdi></td>
                                <td>{item.counts === null ? <span>لم تُحفظ مطابقة بعد</span> : <>
                                    {STATEMENT_COUNTS.map(([key, label]) => <span key={key}>{label}: {item.counts[key]}</span>)}
                                    <small>آخر مطابقة محفوظة: {date(item.reviewed_at)}</small>
                                    {item.review_as_of && <small>مصادرها كما في: {date(item.review_as_of)}</small>}
                                </>}</td>
                                <td><button type="button" onClick={() => read("review", item.id)} disabled={Boolean(busy) || !readable}>عرض المطابقة</button></td>
                            </tr>)}</tbody></table></div>}
                    </PaginatedReportTable>}
                </section>
            </>}
        </>}
    </>;
}
