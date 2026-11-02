import React, { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../../../services/accountingOnboarding";
import { createOnboardingSessionController } from "../../../services/onboardingSessionController";
import { buildFinancialSection, FINANCIAL_STAGE_SECTIONS, restoreFinancialSession } from "./onboardingFinancialAdapter";
import { validateOpeningInventoryRows } from "./OpeningInventoryEditor";
import OnboardingSsotSetup, { contractSection } from "./OnboardingSsotSetup";
import RichShippingContracts from "./RichShippingContracts";
import OnboardingWizardView from "./OnboardingWizardView";
import { getOnboardingInventoryCatalog } from "../../../services/onboardingInventoryCatalog";
import { loadOnboardingContext, stageSourceErrors, FINANCIAL_SECTIONS } from "../../../services/onboardingContext";
import OpeningDomainContext from "./OpeningDomainContext";
import OpeningReview from "./OpeningReview";

export { FINANCIAL_SECTIONS } from "../../../services/onboardingContext";
const LABELS = { banks_cash: "البنوك والصناديق", providers: "مزودو الدفع والإعلانات", couriers_cod: "الشحن والموصلون", inventory: "تقييم المخزون", suppliers: "الموردون والأطراف الخارجية", payroll_obligations: "الموظفون", equity: "المصروفات والالتزامات الأخرى" };
const STATES = { not_started: "لم يبدأ", incomplete: "ناقص", complete: "مكتمل", not_applicable: "لا ينطبق" };
const button = "rounded-lg border px-4 py-2 disabled:opacity-40";
const input = "block w-full rounded-lg border p-2";
const clone = value => JSON.parse(JSON.stringify(value));
const localTime = iso => iso ? new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso)).replace(" ", "T") : "";
function cutoverTime(value) {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value || "")) throw new Error("onboarding_cutover_required");
    return `${value}:00+03:00`;
}

// Stage 10 autosaves setup metadata only, through the versioned session controller.
export default function AccountingOnboarding({ accountingPermissions = [], transport = api, inventoryContext = {}, loadInventory = getOnboardingInventoryCatalog }) {
    const controller = useMemo(() => createOnboardingSessionController(transport), [transport]);
    const [context, setContext] = useState(null), [sessions, setSessions] = useState([]), [selected, setSelected] = useState("");
    const [sourceErrors, setSourceErrors] = useState([]), [loadingContext, setLoadingContext] = useState(true), [contextReload, setContextReload] = useState(0);
    const [pendingSourceRestore, setPendingSourceRestore] = useState([]);
    const [session, setSession] = useState(null), [view, setView] = useState({ sections: {}, couriers: {} });
    const [stage, setStage] = useState("cutover"), [cutover, setCutover] = useState("");
    const [busy, setBusy] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState("");
    const [dirty, setDirty] = useState([]), [metadata, setMetadata] = useState({}), [uploads, setUploads] = useState({});
    const [readiness, setReadiness] = useState(null), [note, setNote] = useState("");
    const [catalog, setCatalog] = useState(inventoryContext);
    const inFlight = useRef(false);
    const catalogRequest = useRef(0);
    const [catalogState, setCatalogState] = useState("idle");
    const [draftMessage, setDraftMessage] = useState("");
    const [draftWriting, setDraftWriting] = useState(false);
    const draftLatest = useRef(null), draftSaved = useRef(""), draftSaving = useRef(null);
    const restoreAttempt = useRef(false);
    const canView = accountingPermissions.includes("accounting.opening_balances.view");
    const canSave = canView && accountingPermissions.includes("accounting.opening_balances.drafts.manage");
    const canReview = canView && accountingPermissions.includes("accounting.opening_balances.review");
    const locked = session && (session.status === "reviewed" || session.status === "handed_off" || session.opening_draft);
    const sectionId = FINANCIAL_STAGE_SECTIONS[stage] || (stage === "payment_fees" && context?.ssotSetupSupported ? "providers" : null);
    const pending = controller.hasPendingRequest();
    const blocked = pending || controller.needsReload();
    const stageErrors = stageSourceErrors(stage, sourceErrors);
    const restoreBlocked = stageId => pendingSourceRestore.some(id => id === stageId || FINANCIAL_STAGE_SECTIONS[id] === (FINANCIAL_STAGE_SECTIONS[stageId] || (stageId === "payment_fees" ? "providers" : null)));
    const sourceBlocked = loadingContext || stageErrors.length > 0 || restoreBlocked(stage);
    const markDirty = id => { setDirty(current => [...new Set([...current, id])]); setReadiness(null); };
    async function refreshCatalog() {
        const request = ++catalogRequest.current;
        setCatalogState("loading");
        try {
            const next = await loadInventory();
            // Only the latest refresh can establish the inventory provenance shown.
            if (request !== catalogRequest.current) return;
            setCatalog(next); setCatalogState("ready");
        } catch (_) { if (request === catalogRequest.current) setCatalogState("error"); }
    }
    useEffect(() => () => { catalogRequest.current += 1; }, []);
    useEffect(() => {
        if (canView && session && stage === "inventory" && catalogState === "idle") refreshCatalog();
    }, [canView, session, stage, catalogState]);
    useEffect(() => {
        if (!context || restoreAttempt.current) return;
        restoreAttempt.current = true;
        const id = new URLSearchParams(window.location.search).get("onboarding_session");
        if (id) run(async () => { accept(await controller.load(id), true); setStage(new URLSearchParams(window.location.search).get("onboarding_stage") || "cutover"); });
    }, [context]);
    useEffect(() => {
        const warn = event => { if (draftLatest.current && JSON.stringify(draftLatest.current) !== draftSaved.current) { event.preventDefault(); event.returnValue = ""; } };
        window.addEventListener("beforeunload", warn);
        return () => window.removeEventListener("beforeunload", warn);
    }, []);
    useEffect(() => {
        if (busy || !session || !canSave || locked || blocked || !draftLatest.current || JSON.stringify(draftLatest.current) === draftSaved.current) return undefined;
        const timer = setTimeout(() => { persistInventory().catch(() => {}); }, 400);
        return () => clearTimeout(timer);
    }, [view, session, canSave, locked, blocked, busy]);
    async function persistInventory() {
        if (draftSaving.current) return draftSaving.current;
        if (!canSave || locked || blocked || !session) throw new Error("onboarding_session_locked");
        setDraftWriting(true);
        draftSaving.current = (async () => {
            while (draftLatest.current && JSON.stringify(draftLatest.current) !== draftSaved.current) {
                const snapshot = clone(draftLatest.current), serialized = JSON.stringify(snapshot);
                setDraftMessage("جارٍ حفظ مسودة المخزون…");
                try {
                    const next = await controller.saveInventoryDraft({ draft: snapshot }, session.id);
                    draftSaved.current = serialized;
                    accept(next);
                    setDraftMessage("مسودة المخزون محفوظة على الخادم؛ يمكن استعادتها بعد التحديث.");
                } catch (err) { setDraftMessage("لم تُحفظ آخر تعديلات المخزون. احتفظ بالصفحة وأعد المحاولة أو استعد الجلسة."); setError(api.onboardingErrorMessage(err)); throw err; }
            }
        })();
        try { await draftSaving.current; } finally { draftSaving.current = null; setDraftWriting(false); }
    }
    async function navigate(next) {
        if (stage === "inventory" && draftLatest.current && JSON.stringify(draftLatest.current) !== draftSaved.current) {
            try { await persistInventory(); } catch (_) { return; }
        }
        setStage(next);
        const url = new URL(window.location.href); url.searchParams.set("onboarding_stage", next); window.history.replaceState(null, "", url);
    }
    useEffect(() => {
        if (!canView) return undefined;
        let active = true;
        setLoadingContext(true);
        loadOnboardingContext(transport).then(result => {
            if (!active) return;
            setContext(result.context); setSessions(result.sessions); setSourceErrors(result.errors); setLoadingContext(false);
        }).catch(err => { if (active) { setSourceErrors([{source: "definitions", label: "تعريفات التأسيس", message: api.onboardingErrorMessage(err)}]); setLoadingContext(false); } });
        return () => { active = false; };
    }, [transport, canView, contextReload]);

    useEffect(() => {
        if (loadingContext || !context || !session || !pendingSourceRestore.length) return;
        const recovered = pendingSourceRestore.filter(id => !stageSourceErrors(id, sourceErrors).length);
        if (!recovered.length) return;
        // A partial catalogue cannot faithfully restore every financial row.
        // Rehydrate only those blocked stages from the current saved snapshot
        // before unlocking them; preserve unrelated local drafts and evidence.
        setView(current => {
            const restored = restoreFinancialSession(session, current, context);
            return { ...current, sections: { ...current.sections, ...Object.fromEntries(recovered.map(id => [id, restored.sections[id]])) },
                ...(recovered.includes("courier_balances") ? { couriers: restored.couriers } : {}) };
        });
        setPendingSourceRestore(current => current.filter(id => !recovered.includes(id)));
    }, [context, session, sourceErrors, loadingContext, pendingSourceRestore]);

    async function run(task) {
        if (inFlight.current) return;
        inFlight.current = true; setBusy(true); setError(""); setMessage("");
        try { await task(); } catch (err) { setError(api.onboardingErrorMessage(err)); }
        finally { inFlight.current = false; setBusy(false); }
    }
    function accept(next, restore = false) {
        setSession(next); setSelected(next.id); setReadiness(null);
        const url = new URL(window.location.href); url.searchParams.set("onboarding_session", next.id); window.history.replaceState(null, "", url);
        setSessions(current => [...current.filter(s => s.id !== next.id), next]);
        if (!restore && next.inventory_draft && JSON.stringify(next.inventory_draft) === JSON.stringify(draftLatest.current)) draftSaved.current = JSON.stringify(draftLatest.current);
        if (restore) {
            setPendingSourceRestore(Object.keys(FINANCIAL_STAGE_SECTIONS).filter(id => stageSourceErrors(id, sourceErrors).length));
            const restored = restoreFinancialSession(next, {}, context);
            if (restored.sections.cutover) restored.sections.cutover.cutover_at = localTime(next.cutover?.cutover_at);
            if (next.inventory_draft) restored.sections.inventory = { ...restored.sections.inventory, ...clone(next.inventory_draft) };
            draftLatest.current = next.inventory_draft ? clone(next.inventory_draft) : null;
            draftSaved.current = next.inventory_draft ? JSON.stringify(next.inventory_draft) : "";
            setDraftMessage(next.inventory_draft ? "مسودة المخزون مستعادة من الخادم." : "");
            setView(restored); setMetadata(clone(next.sections || {})); setDirty([]); setUploads({});
        }
    }
    function change(next) {
        setView(next);
        if (stage === "inventory") {
            draftLatest.current = { rows: next.sections.inventory?.rows || [], financial_lines: next.sections.inventory?.financial_lines || [] };
            setDraftMessage("تعديلات مخزون قيد الحفظ…");
        }
        const target = FINANCIAL_STAGE_SECTIONS[stage] || (stage === "courier_contracts" ? "couriers_cod" : stage === "cutover" ? "cutover" : null);
        if (target) { markDirty(target); setMetadata(current => ({ ...current, [target]: { ...current[target], status: "incomplete" } })); }
    }
    function editMetadata(changes) {
        setMetadata(current => ({ ...current, [sectionId]: { ...current[sectionId], ...changes } })); markDirty(sectionId);
    }
    async function upload(file) {
        if (!file) return;
        await run(async () => {
            const slot = stage === "cutover" ? "cutover" : sectionId;
            const artifact = await transport.uploadOnboardingEvidence({ file, financialBase: context.financial_base, purpose: slot === "cutover" ? "cutover" : "opening_balance", ...(slot !== "cutover" ? { sectionId: slot } : {}) });
            if (!artifact.source_file_id || !/^[a-f0-9]{64}$/.test(artifact.sha256 || "")) throw new Error("opening_evidence_contract_mismatch");
            setUploads(current => ({ ...current, [slot]: artifact }));
            setMetadata(current => ({ ...current, [slot]: { ...current[slot], evidence_file_id: artifact.source_file_id } }));
            markDirty(slot); setMessage("حُفظ ملف الدليل؛ احفظ القسم لربطه بالجلسة.");
        });
    }
    async function save(stageId) {
        if (!canSave || locked || !session || loadingContext || stageSourceErrors(stageId, sourceErrors).length || restoreBlocked(stageId)) return;
        if (stageId === "courier_contracts" || (stageId === "payment_fees" && !context.ssotSetupSupported)) { setMessage("مسودة النطاق في الذاكرة فقط. احفظ أرصدة الشحن من المرحلة 8؛ شروط العقد ليست ضمن الحفظ المالي."); return; }
        if (stageId === "inventory") { try { await persistInventory(); } catch (_) { return; } }
        await run(async () => {
            if (stageId === "cutover") {
                const next = await controller.saveCutover({ cutover_at: cutoverTime(view.sections.cutover?.cutover_at), cutover_timezone: "Asia/Riyadh", cutover_evidence_file_id: metadata.cutover?.evidence_file_id || session.cutover?.cutover_evidence_file_id || null });
                accept(next); setView(current => ({ ...current, sections: { ...current.sections, cutover: { ...current.sections.cutover, status: next.cutover.cutover_evidence_file_id ? "complete" : "incomplete", evidence_ref: next.cutover.cutover_evidence_file_id || "" } } })); setDirty(current => current.filter(id => id !== "cutover"));
            } else {
                const id = FINANCIAL_STAGE_SECTIONS[stageId] || (stageId === "payment_fees" ? "providers" : null), meta = metadata[id] || {};
                if (id === "inventory" && meta.status === "complete" && validateOpeningInventoryRows(view.sections.inventory?.rows || [], catalog).length) throw new Error("onboarding_inventory_draft_incomplete");
                const evidence = meta.evidence_file_id || null;
                const projection = clone(view);
                if (id === "inventory") {
                    // Product rows are independently persisted setup metadata, never ledger input.
                    delete projection.sections.inventory.rows;
                    projection.sections.inventory.financial_lines = (projection.sections.inventory.financial_lines || []).map(line => ({ ...line, ...(evidence ? { evidence_file_id: evidence } : {}) }));
                }
                // Shared section state applies to ALL sibling screens, never one stage alone.
                for (const [s, section] of Object.entries(FINANCIAL_STAGE_SECTIONS)) if (section === id && projection.sections[s]) projection.sections[s].status = "incomplete";
                const { data } = context.ssotSetupSupported && ["payment_fees", "prepaid", "obligations"].includes(stageId) ? { data: clone(session.sections[id].data) } : buildFinancialSection(stageId, projection, session.sections[id], context, { evidenceFileId: evidence, manifestHash: uploads[id]?.sha256 });
                if (meta.status === "not_applicable" && (data.lines.length || data.provider_bindings?.length || !meta.reason?.trim() || !evidence)) throw new Error("onboarding_not_applicable_conflict");
                if (meta.status === "not_applicable") delete data.inventory_valuation;
                const next = await controller.saveSection(id, { status: meta.status || "incomplete", reason: meta.reason || "", evidence_file_id: evidence, data });
                accept(next); setMetadata(current => ({ ...current, [id]: next.sections[id] }));
                setView(current => ({ ...current, sections: Object.fromEntries(Object.entries(current.sections).map(([key, value]) => [key, FINANCIAL_STAGE_SECTIONS[key] === id ? { ...value, explicit_zero: next.sections[id].data.lines.length > 0 && next.sections[id].data.lines.every(line => line.meaning === "zero"), status: next.sections[id].status, evidence_ref: next.sections[id].evidence_file_id || "", not_applicable_reason: next.sections[id].reason || "" } : value])) }));
                setDirty(current => current.filter(key => key !== id));
            }
            setMessage("حُفظت البيانات المالية في الجلسة. حقول النطاق المحلية لا يشملها هذا الحفظ.");
        });
    }
    async function createPerson(person) {
        if (inFlight.current || !canSave || locked || blocked) throw new Error("onboarding_session_locked");
        inFlight.current = true; setBusy(true);
        try { return await transport.createOnboardingExternalPerson(person); }
        finally { inFlight.current = false; setBusy(false); }
    }
    const read = () => run(async () => { const result = await transport.getOnboardingReadiness(session.id); setReadiness(result); });
    if (!canView) return <p role="alert" dir="rtl">صلاحية عرض الأرصدة الافتتاحية مطلوبة.</p>;
    return <div dir="rtl" lang="ar" className="space-y-4" data-testid="accounting-onboarding">
        <section className="space-y-3 rounded-xl border bg-white p-4">
            <h2 className="text-xl font-bold">جلسة التأسيس المحفوظة</h2>
            <p>الحفظ المالي يشمل 7 أقسام أدلة عبر 16 شاشة. تُحفظ مسودة المخزون تلقائيًا في بيانات الجلسة فقط.</p>
            <p className="rounded-lg bg-amber-50 p-3">تُحفظ شروط عقود الشحن المعتمدة عبر مسار العقود المستقل. مراجع تمويل الإعلان وملاحظات البنود تبقى محلية؛ لا تدخل الحفظ المالي.</p>
            {!session && <><label>لحظة القطع للجلسة الجديدة — الرياض<input aria-label="لحظة القطع للجلسة الجديدة" type="datetime-local" className={input} value={cutover} onChange={e => setCutover(e.target.value)} /></label><button type="button" className={button} disabled={!context || !canSave || busy || pending || !cutover} onClick={() => run(async () => accept(await controller.create({ cutover_at: cutoverTime(cutover), cutover_timezone: "Asia/Riyadh" }), true))}>إنشاء جلسة</button></>}
            <label>الجلسات المحفوظة<select aria-label="الجلسات المحفوظة" className={input} value={selected} disabled={busy || draftWriting} onChange={e => setSelected(e.target.value)}><option value="">اختر جلسة</option>{sessions.map(s => <option key={s.id} value={s.id}>{s.id} · {s.status} · {s.version}</option>)}</select></label>
            <button type="button" className={button} disabled={!context || !selected || busy || draftWriting} onClick={() => run(async () => accept(await controller.load(selected), true))}>استعادة المحفوظ وتجاهل التعديلات المحلية</button>
            {pending && !controller.needsReload() && <button className={button} disabled={busy} onClick={() => run(async () => { accept(await controller.retry(), !session); setMessage("استُعيد رد الطلب الأصلي؛ تحقق من المحفوظ قبل متابعة التحرير."); })}>إعادة إرسال الطلب نفسه</button>}
            {controller.needsReload() && <p role="alert">يلزم استعادة الجلسة قبل حفظ جديد. التعديلات الحالية لم تُكتب فوق نسخة الخادم.</p>}
            {session && <p role="status">الجلسة <bdi className="break-all">{session.id}</bdi> · الإصدار {session.version} · {locked ? "مراجعة ومقفلة" : session.status} · {dirty.length ? "تعديلات مالية غير محفوظة" : "النسخة المالية محفوظة"}</p>}
        </section>
        {error && <p role="alert" className="rounded-lg bg-rose-50 p-4">{error}</p>}
        {message && <p role="status">{message}</p>}
        {loadingContext && <p role="status">جارٍ تحميل مصادر التأسيس…</p>}
        {sourceErrors.length > 0 && <section aria-label="أخطاء مصادر التأسيس" className="space-y-2 rounded-xl border border-amber-300 bg-amber-50 p-4"><p>تعذر تحميل بعض المصادر. بقية المراحل متاحة؛ يتوقف حفظ القسم الذي يعتمد على المصدر الناقص.</p><ul>{sourceErrors.map(item => <li key={item.source} role="alert">مصدر {item.label}: {item.message}</li>)}</ul><button type="button" className={button} disabled={busy || draftWriting || loadingContext} onClick={() => setContextReload(n => n + 1)}>إعادة تحميل مصادر التأسيس</button></section>}
        {session && context && <>
            <section aria-label="تقدم الأقسام المالية" className="grid gap-2 rounded-xl border p-4 sm:grid-cols-2 lg:grid-cols-4"><progress aria-label="تقدم الأقسام المالية" max="7" value={FINANCIAL_SECTIONS.filter(id => ["complete", "not_applicable"].includes(session.sections[id]?.status)).length} />{FINANCIAL_SECTIONS.map(id => <p key={id}>{LABELS[id]}: {STATES[session.sections[id]?.status] || STATES.not_started}{dirty.includes(id) ? " · تعديلات غير محفوظة" : ""}{!session.sections[id]?.evidence_file_id ? " · دليل ناقص" : ""}</p>)}</section>
            {stage === "inventory" && <section aria-label="حالة كتالوج المخزون" className="space-y-2 rounded-xl border bg-white p-4">
                {catalogState === "loading" && <p role="status">جارٍ تحميل كتالوج V2…</p>}
                {catalogState === "error" && <p role="alert">تعذر تحميل الكتالوج. بيانات المسودة محفوظة؛ أعد المحاولة.</p>}
                <button type="button" className={button} disabled={catalogState === "loading"} onClick={refreshCatalog}>{catalogState === "error" ? "إعادة محاولة تحميل الكتالوج" : "تحديث الكتالوج"}</button>
                {catalogState === "ready" && <p role="status">الكتالوج: {catalog.products?.length || 0} منتج · {catalog.components?.length || 0} مكوّن · {catalog.locations?.length || 0} خانة</p>}
            </section>}
            <div className="min-w-0">
                <OnboardingWizardView domainContent={["courier_contracts", "courier_balances", "drivers", "payment_fees", "prepaid", "obligations"].includes(stage) ? <OpeningDomainContext key={stage} stage={stage} transport={transport} /> : null} reviewContent={<OpeningReview session={session} dirty={dirty.length > 0} readiness={readiness} />} richShippingContent={stage === "courier_contracts" ? <RichShippingContracts transport={transport} permissions={accountingPermissions} financialBase={context.financial_base} value={view.couriers} banks={context.entities?.banks || []} onChange={next => change({ ...view, couriers: next })} onBusyChange={value => { inFlight.current = value; setBusy(value); }} /> : null} richShippingSupported setupContent={context.ssotSetupSupported && ["payment_fees", "prepaid", "obligations"].includes(stage) ? <OnboardingSsotSetup key={stage} stage={stage} session={session} transport={transport} onBusyChange={value => { inFlight.current = value; setBusy(value); }} disabled={Boolean(busy || locked || !canSave || blocked || sourceBlocked || dirty.length)} onError={err => setError(api.onboardingErrorMessage(err))} onSelect={async contract => { const id = stage === "payment_fees" ? "providers" : "equity"; const next = await controller.saveSection(id, contractSection(session, stage, contract)); accept(next, true); }} /> : null} financialBinding busy={busy} readOnly={Boolean(locked || !canSave || blocked || sourceBlocked)} value={view} onChange={change} activeStage={stage} onStageChange={navigate} context={{ ...context, inventory: catalog }} onSaveSection={save} onCreateExternalPerson={createPerson} onEntityCreated={person => setContext(current => ({ ...current, entities: { ...current.entities, external_persons: [...current.entities.external_persons, { ...person, name: person.name || person.label }] } }))} />
            </div>
            {stage === "inventory" && <fieldset disabled={busy || locked || !canSave || blocked || sourceBlocked} className="space-y-3 rounded-xl border p-4">
                <legend>التقييم المالي لكل حساب مخزون</legend>
                <button type="button" className={button} onClick={() => persistInventory().catch(() => {})}>حفظ مسودة المخزون الآن</button>
                {draftMessage && <p role="status">{draftMessage}</p>}
                {catalog.warnings?.length > 0 && <p role="status">الخانات ذات المنشأ غير المثبت تظهر AMBIGUOUS. ربط حسابات المخزون يتطلب مرجع حساب موثقًا.</p>}
                <p>أدخل مرجع حساب موثقًا وقيمته وفق ملف التقييم الأصلي. الكميات والخيارات والتوزيع محفوظة كمسودة إعداد مستقلة؛ هذه القيم هي مدخلات التقييم المالي. بعد تعديل تقييم مستعاد، ارفع ملف التقييم من جديد قبل إكمال القسم.</p>
                {(view.sections.inventory?.financial_lines || []).map((line, i, rows) => <div key={i} className="grid gap-2 md:grid-cols-3">
                    <label>مرجع حساب المخزون<input aria-label={`مرجع حساب المخزون ${i + 1}`} className={input} value={line.entity_id || ""} onChange={e => change({ ...view, sections: { ...view.sections, inventory: { ...view.sections.inventory, financial_lines: rows.map((row, j) => j === i ? { ...row, entity_id: e.target.value } : row) } } })} /></label>
                    <label>القيمة بالريال<input aria-label={`قيمة حساب المخزون ${i + 1}`} type="number" min="0" step="0.01" className={input} value={line.original_amount ?? ""} onChange={e => change({ ...view, sections: { ...view.sections, inventory: { ...view.sections.inventory, financial_lines: rows.map((row, j) => j === i ? { ...row, original_amount: e.target.value, meaning: /^0+(\.0+)?$/.test(e.target.value) ? "zero" : "available_to_us" } : row) } } })} /></label>
                    <button className={button} onClick={() => change({ ...view, sections: { ...view.sections, inventory: { ...view.sections.inventory, financial_lines: rows.filter((_, j) => j !== i) } } })}>حذف قيمة الحساب</button>
                </div>)}
                <button className={button} onClick={() => change({ ...view, sections: { ...view.sections, inventory: { ...view.sections.inventory, financial_lines: [...(view.sections.inventory?.financial_lines || []), { category: "inventory_asset", entity_id: "", original_currency: "SAR", fx_rate_to_sar: "1" }] } } })}>إضافة قيمة حساب مخزون</button>
            </fieldset>}
            {(sectionId || stage === "cutover") && <fieldset disabled={busy || locked || !canSave || blocked || sourceBlocked} className="space-y-3 rounded-xl border p-4">
                <legend>{stage === "cutover" ? "حفظ لحظة القطع" : `حفظ القسم المالي المشترك: ${LABELS[sectionId]}`}</legend>
                {sectionId && <><p>هذا الحفظ يشمل الشاشات المرتبطة بهذا القسم، ويحافظ على بنودها جميعًا.</p><label>حالة القسم المالي<select aria-label="حالة القسم المالي" className={input} value={metadata[sectionId]?.status || "not_started"} onChange={e => editMetadata({ status: e.target.value })}>{Object.entries(STATES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><label>سبب / ملاحظة القسم<input aria-label="سبب القسم المالي" className={input} value={metadata[sectionId]?.reason || ""} onChange={e => editMetadata({ reason: e.target.value })} /></label></>}
                <label>رفع أصل دليل {sectionId ? LABELS[sectionId] : "القطع"}<input type="file" aria-label="رفع دليل القسم المالي" onChange={e => upload(e.target.files?.[0])} /></label>
                <p>الدليل المحفوظ: {metadata[sectionId || "cutover"]?.evidence_file_id || (stage === "cutover" && session.cutover?.cutover_evidence_file_id) || "دليل ناقص"}</p>
                <button type="button" className={button} onClick={() => save(stage)}>حفظ البيانات المالية</button>
            </fieldset>}
            <section className="space-y-3 rounded-xl border p-4" aria-label="معاينة ومراجعة الخادم">
                <label>ملاحظة المعاينة والمراجعة<input aria-label="ملاحظة المعاينة والمراجعة" className={input} value={note} onChange={e => setNote(e.target.value)} /></label>
                <button className={button} disabled={busy || locked || !canSave || blocked || stageSourceErrors("review", sourceErrors).length > 0 || dirty.length > 0 || note.trim().length < 3} onClick={() => run(async () => accept(await controller.preview(note)))}>معاينة الجلسة على الخادم</button>
                <button className={button} disabled={busy || locked || !canReview || blocked || stageSourceErrors("review", sourceErrors).length > 0 || dirty.length > 0 || session.status !== "previewed" || note.trim().length < 3} onClick={() => run(async () => accept(await controller.review(note)))}>مراجعة الجلسة وقفلها</button>
                <button className={button} disabled={busy || dirty.length > 0} onClick={read}>فحص جاهزية المصدر</button>
                {session.preview && dirty.length === 0 && <div data-testid="server-preview"><p>معاينة الخادم: مدين {session.preview.debit_total} · دائن {session.preview.credit_total} · {session.preview.balanced === true ? "متوازن" : "مشكلة مطابقة"}</p><p>حسابات الصفر الصريح: {session.preview.zero_accounts?.length ?? 0}</p><p>مطابقة تقييم المخزون: {session.preview.inventory_reconciliation?.verified === true ? "مكتمل" : "غير مثبتة"}</p></div>}
                {readiness && <div data-testid="server-readiness"><p>جاهزية المصدر: {readiness.source_ready === true ? "مكتمل" : "ناقص"}</p><p>مطابقة التقييم المالي: {readiness.inventory_reconciled === true ? "مكتمل" : "غير مثبتة"}</p><p>اعتماد الكميات الفعلية: {readiness.inventory_physical_approval_verified === true ? "مثبت" : "غير مثبت"}</p><p>Smoke B: {readiness.live_gates?.smoke_b === "BLOCKED_BY_ENVIRONMENT" ? "إثبات بيئة التشغيل المطلوبة غير مكتمل؛ نجاح الاختبار المعزول لا يفتح التشغيل المالي." : "يلزم مراجعة دليل بيئة التشغيل؛ لا يُستنتج القبول من جاهزية المسودة."}</p><p>جاهزية الترحيل الفعلي: {readiness.ready_for_live_post === true ? "أبلغ المصدر عن الجاهزية؛ التنفيذ والتفويض مستقلان وخارج هذه الشاشة." : "غير متاحة؛ تبقى بوابات التنفيذ والتفويض مطلوبة."}</p><p>موانع الخادم: {readiness.blockers?.length ?? "غير معروفة"}</p><ul>{(readiness.blockers || []).map((blocker, index) => <li key={index}>{LABELS[blocker.section_id] || "المراجعة العامة"}: {api.onboardingErrorMessage({ response: { data: { detail: { code: blocker.code } } } })}</li>)}</ul></div>}
                <p>P02 — LOCKED. لا ترحيل أو تفعيل من هذا المعالج.</p>
            </section>
        </>}
    </div>;
}
