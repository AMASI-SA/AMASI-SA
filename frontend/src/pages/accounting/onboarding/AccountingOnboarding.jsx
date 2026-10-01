import React, { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../../../services/accountingOnboarding";
import { createOnboardingSessionController } from "../../../services/onboardingSessionController";
import { buildFinancialSection, FINANCIAL_STAGE_SECTIONS, restoreFinancialSession } from "./onboardingFinancialAdapter";
import OnboardingWizardView from "./OnboardingWizardView";
import { getOnboardingInventoryCatalog } from "../../../services/onboardingInventoryCatalog";

export const FINANCIAL_SECTIONS = ["banks_cash", "providers", "couriers_cod", "inventory", "suppliers", "payroll_obligations", "equity"];
const LABELS = { banks_cash: "البنوك والصناديق", providers: "مزودو الدفع والإعلانات", couriers_cod: "الشحن والموصلون", inventory: "تقييم المخزون", suppliers: "الموردون والأطراف الخارجية", payroll_obligations: "الموظفون", equity: "المصروفات والالتزامات الأخرى" };
const STATES = { not_started: "لم يبدأ", incomplete: "ناقص", complete: "مكتمل", not_applicable: "لا ينطبق" };
const KINDS = { bank: "banks", provider: "payment_providers", employee: "employees", supplier: "suppliers", external_person: "external_persons", courier: "couriers", store_driver: "store_drivers", ad_account: "ad_accounts" };
const button = "rounded-lg border px-4 py-2 disabled:opacity-40";
const input = "block w-full rounded-lg border p-2";
const clone = value => JSON.parse(JSON.stringify(value));
const activeFinancialIdentity = account => account.status === "active"
    && !["archived", "is_archived", "deleted", "is_deleted"].some(key => account[key] === true)
    && !["active", "is_active"].some(key => account[key] === false);
const localTime = iso => iso ? new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso)).replace(" ", "T") : "";
function cutoverTime(value) {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value || "")) throw new Error("onboarding_cutover_required");
    return `${value}:00+03:00`;
}

// Only Track A setup metadata is saved. No autosave, localStorage, ledger or live actions.
export default function AccountingOnboarding({ accountingPermissions = [], transport = api, inventoryContext = {}, loadInventory = getOnboardingInventoryCatalog }) {
    const controller = useMemo(() => createOnboardingSessionController(transport), [transport]);
    const [context, setContext] = useState(null), [sessions, setSessions] = useState([]), [selected, setSelected] = useState("");
    const [session, setSession] = useState(null), [view, setView] = useState({ sections: {}, couriers: {} });
    const [stage, setStage] = useState("cutover"), [cutover, setCutover] = useState("");
    const [busy, setBusy] = useState(false), [error, setError] = useState(""), [message, setMessage] = useState("");
    const [dirty, setDirty] = useState([]), [metadata, setMetadata] = useState({}), [uploads, setUploads] = useState({});
    const [readiness, setReadiness] = useState(null), [note, setNote] = useState("");
    const [catalog, setCatalog] = useState(inventoryContext);
    const inFlight = useRef(false);
    const canView = accountingPermissions.includes("accounting.opening_balances.view");
    const canSave = canView && accountingPermissions.includes("accounting.opening_balances.drafts.manage");
    const canReview = canView && accountingPermissions.includes("accounting.opening_balances.review");
    const locked = session && (session.status === "reviewed" || session.status === "handed_off" || session.opening_draft);
    const sectionId = FINANCIAL_STAGE_SECTIONS[stage];
    const pending = controller.hasPendingRequest();
    const blocked = pending || controller.needsReload();
    const markDirty = id => { setDirty(current => [...new Set([...current, id])]); setReadiness(null); };
    useEffect(() => {
        if (!canView) return undefined;
        let active = true;
        Promise.all([transport.getOnboardingDefinitions(), transport.listOnboardingSessions(), ...Object.keys(KINDS).map(kind => transport.getOnboardingIdentities(kind))]).then(async ([definitions, listing, ...identities]) => {
            if (!active) return;
            if (definitions.schema_version !== 1 || !FINANCIAL_SECTIONS.every(id => definitions.sections?.includes(id))) throw new Error("onboarding_contract_invalid");
            const accounts = await transport.getOnboardingFinancialAccounts(definitions.financial_base);
            if (!active) return;
            const allAccounts = accounts.items.map(a => ({ ...a, name: a.name || a.label }));
            const entities = Object.fromEntries(Object.values(KINDS).map((key, i) => [key, identities[i].items.map(item => ({ ...item, name: item.label }))]));
            entities.financial_accounts = allAccounts.filter(a => activeFinancialIdentity(a) && ["bank", "cash", "overdraft"].includes(a.account_type));
            entities.banks = allAccounts.filter(a => activeFinancialIdentity(a) && a.account_type === "bank" && a.currency === "SAR");
            const categories = Object.entries(definitions.opening_categories || {}).map(([id, info]) => ({ id, ...info }));
            setContext({ financial_base: definitions.financial_base, entities, financial_accounts: allAccounts, classifications: { prepaid: categories.filter(c => c.id === "prepaid_expense"), obligations: categories.filter(c => ["accrued_expense", "other_receivable", "other_payable", "input_vat", "sales_vat_payable"].includes(c.id)) }, feeConfigurationSupported: false });
            setSessions(listing.items);
        }).catch(err => { if (active) setError(api.onboardingErrorMessage(err)); });
        return () => { active = false; };
    }, [transport, canView]);

    async function run(task) {
        if (inFlight.current) return;
        inFlight.current = true; setBusy(true); setError(""); setMessage("");
        try { await task(); } catch (err) { setError(api.onboardingErrorMessage(err)); }
        finally { inFlight.current = false; setBusy(false); }
    }
    function accept(next, restore = false) {
        setSession(next); setSelected(next.id); setReadiness(null);
        setSessions(current => [...current.filter(s => s.id !== next.id), next]);
        if (restore) {
            const restored = restoreFinancialSession(next, {}, context);
            if (restored.sections.cutover) restored.sections.cutover.cutover_at = localTime(next.cutover?.cutover_at);
            setView(restored); setMetadata(clone(next.sections || {})); setDirty([]); setUploads({});
        }
    }
    function change(next) {
        setView(next);
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
        if (!canSave || locked || !session) return;
        if (stageId === "courier_contracts" || stageId === "payment_fees") { setMessage("مسودة النطاق في الذاكرة فقط. احفظ أرصدة الشحن من المرحلة 8؛ شروط العقد ليست ضمن الحفظ المالي."); return; }
        await run(async () => {
            if (stageId === "cutover") {
                const next = await controller.saveCutover({ cutover_at: cutoverTime(view.sections.cutover?.cutover_at), cutover_timezone: "Asia/Riyadh", cutover_evidence_file_id: metadata.cutover?.evidence_file_id || session.cutover?.cutover_evidence_file_id || null });
                accept(next); setView(current => ({ ...current, sections: { ...current.sections, cutover: { ...current.sections.cutover, status: next.cutover.cutover_evidence_file_id ? "complete" : "incomplete", evidence_ref: next.cutover.cutover_evidence_file_id || "" } } })); setDirty(current => current.filter(id => id !== "cutover"));
            } else {
                const id = FINANCIAL_STAGE_SECTIONS[stageId], meta = metadata[id] || {};
                const evidence = meta.evidence_file_id || null;
                const projection = clone(view);
                if (id === "inventory") {
                    // Valuation-only fields are explicit financial facts; physical rows stay local.
                    delete projection.sections.inventory.rows;
                    projection.sections.inventory.financial_lines = (projection.sections.inventory.financial_lines || []).map(line => ({ ...line, ...(evidence ? { evidence_file_id: evidence } : {}) }));
                }
                // Shared section state applies to ALL sibling screens, never one stage alone.
                for (const [s, section] of Object.entries(FINANCIAL_STAGE_SECTIONS)) if (section === id && projection.sections[s]) projection.sections[s].status = "incomplete";
                const { data } = buildFinancialSection(stageId, projection, session.sections[id], context, { evidenceFileId: evidence, manifestHash: uploads[id]?.sha256 });
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
            <p>الحفظ المالي يشمل 7 أقسام أدلة عبر 16 شاشة. لا تُجرى كتابات تلقائية عند التحرير أو التنقل.</p>
            <p className="rounded-lg bg-amber-50 p-3">شروط عقود الشحن وكميات المخزون وتوزيعاته ومراجع تمويل الإعلان وملاحظات البنود تبقى في ذاكرة هذه الصفحة فقط، وتُفقد عند إعادة التحميل أو استعادة جلسة. لا تدخل الحفظ المالي.</p>
            {!session && <><label>لحظة القطع للجلسة الجديدة — الرياض<input aria-label="لحظة القطع للجلسة الجديدة" type="datetime-local" className={input} value={cutover} onChange={e => setCutover(e.target.value)} /></label><button type="button" className={button} disabled={!context || !canSave || busy || pending || !cutover} onClick={() => run(async () => accept(await controller.create({ cutover_at: cutoverTime(cutover), cutover_timezone: "Asia/Riyadh" }), true))}>إنشاء جلسة</button></>}
            <label>الجلسات المحفوظة<select aria-label="الجلسات المحفوظة" className={input} value={selected} disabled={busy} onChange={e => setSelected(e.target.value)}><option value="">اختر جلسة</option>{sessions.map(s => <option key={s.id} value={s.id}>{s.id} · {s.status} · {s.version}</option>)}</select></label>
            <button type="button" className={button} disabled={!context || !selected || busy} onClick={() => run(async () => accept(await controller.load(selected), true))}>استعادة المحفوظ وتجاهل التعديلات المحلية</button>
            {pending && !controller.needsReload() && <button className={button} disabled={busy} onClick={() => run(async () => { accept(await controller.retry(), !session); setMessage("استُعيد رد الطلب الأصلي؛ تحقق من المحفوظ قبل متابعة التحرير."); })}>إعادة إرسال الطلب نفسه</button>}
            {controller.needsReload() && <p role="alert">يلزم استعادة الجلسة قبل حفظ جديد. التعديلات الحالية لم تُكتب فوق نسخة الخادم.</p>}
            {session && <p role="status">الجلسة <bdi className="break-all">{session.id}</bdi> · الإصدار {session.version} · {locked ? "مراجعة ومقفلة" : session.status} · {dirty.length ? "تعديلات مالية غير محفوظة" : "النسخة المالية محفوظة"}</p>}
        </section>
        {error && <p role="alert" className="rounded-lg bg-rose-50 p-4">{error}</p>}
        {message && <p role="status">{message}</p>}
        {session && context && <>
            <section aria-label="تقدم الأقسام المالية" className="rounded-xl border p-4"><progress max="7" value={FINANCIAL_SECTIONS.filter(id => ["complete", "not_applicable"].includes(session.sections[id]?.status)).length} />{FINANCIAL_SECTIONS.map(id => <p key={id}>{LABELS[id]}: {STATES[session.sections[id]?.status] || STATES.not_started}{dirty.includes(id) ? " · تعديلات غير محفوظة" : ""}{!session.sections[id]?.evidence_file_id ? " · دليل ناقص" : ""}</p>)}</section>
            <div className="min-w-0">
                <OnboardingWizardView financialBinding busy={busy} readOnly={Boolean(locked || !canSave || blocked)} value={view} onChange={change} activeStage={stage} onStageChange={setStage} context={{ ...context, inventory: catalog }} onSaveSection={save} onCreateExternalPerson={createPerson} onEntityCreated={person => setContext(current => ({ ...current, entities: { ...current.entities, external_persons: [...current.entities.external_persons, { ...person, name: person.name || person.label }] } }))} />
            </div>
            {stage === "inventory" && <fieldset disabled={busy || locked || !canSave || blocked} className="space-y-3 rounded-xl border p-4">
                <legend>التقييم المالي لكل حساب مخزون</legend>
                <button className={button} onClick={() => run(async () => setCatalog(await loadInventory()))}>تحميل كتالوج المخزون الحالي</button>
                {catalog.warnings?.length > 0 && <p role="status">الكتالوج محدود؛ قد توجد بنود إضافية خارج العرض الحالي.</p>}
                <p>أدخل مرجع حساب موثقًا وقيمته وفق ملف التقييم الأصلي. هذه القيم وحدها تُحفظ ماليًا؛ الكميات والخانات لا تُرسل. بعد تعديل تقييم مستعاد، ارفع ملف التقييم من جديد قبل إكمال القسم.</p>
                {(view.sections.inventory?.financial_lines || []).map((line, i, rows) => <div key={i} className="grid gap-2 md:grid-cols-3">
                    <label>مرجع حساب المخزون<input aria-label={`مرجع حساب المخزون ${i + 1}`} className={input} value={line.entity_id || ""} onChange={e => change({ ...view, sections: { ...view.sections, inventory: { ...view.sections.inventory, financial_lines: rows.map((row, j) => j === i ? { ...row, entity_id: e.target.value } : row) } } })} /></label>
                    <label>القيمة بالريال<input aria-label={`قيمة حساب المخزون ${i + 1}`} type="number" min="0" step="0.01" className={input} value={line.original_amount ?? ""} onChange={e => change({ ...view, sections: { ...view.sections, inventory: { ...view.sections.inventory, financial_lines: rows.map((row, j) => j === i ? { ...row, original_amount: e.target.value, meaning: /^0+(\.0+)?$/.test(e.target.value) ? "zero" : "available_to_us" } : row) } } })} /></label>
                    <button className={button} onClick={() => change({ ...view, sections: { ...view.sections, inventory: { ...view.sections.inventory, financial_lines: rows.filter((_, j) => j !== i) } } })}>حذف قيمة الحساب</button>
                </div>)}
                <button className={button} onClick={() => change({ ...view, sections: { ...view.sections, inventory: { ...view.sections.inventory, financial_lines: [...(view.sections.inventory?.financial_lines || []), { category: "inventory_asset", entity_id: "", original_currency: "SAR", fx_rate_to_sar: "1" }] } } })}>إضافة قيمة حساب مخزون</button>
            </fieldset>}
            {(sectionId || stage === "cutover") && <fieldset disabled={busy || locked || !canSave || blocked} className="space-y-3 rounded-xl border p-4">
                <legend>{stage === "cutover" ? "حفظ لحظة القطع" : `حفظ القسم المالي المشترك: ${LABELS[sectionId]}`}</legend>
                {sectionId && <><p>هذا الحفظ يشمل الشاشات المرتبطة بهذا القسم، ويحافظ على بنودها جميعًا.</p><label>حالة القسم المالي<select aria-label="حالة القسم المالي" className={input} value={metadata[sectionId]?.status || "not_started"} onChange={e => editMetadata({ status: e.target.value })}>{Object.entries(STATES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label><label>سبب / ملاحظة القسم<input aria-label="سبب القسم المالي" className={input} value={metadata[sectionId]?.reason || ""} onChange={e => editMetadata({ reason: e.target.value })} /></label></>}
                <label>رفع أصل دليل {sectionId ? LABELS[sectionId] : "القطع"}<input type="file" aria-label="رفع دليل القسم المالي" onChange={e => upload(e.target.files?.[0])} /></label>
                <p>الدليل المحفوظ: {metadata[sectionId || "cutover"]?.evidence_file_id || (stage === "cutover" && session.cutover?.cutover_evidence_file_id) || "دليل ناقص"}</p>
                <button type="button" className={button} onClick={() => save(stage)}>حفظ البيانات المالية</button>
            </fieldset>}
            <section className="space-y-3 rounded-xl border p-4" aria-label="معاينة ومراجعة الخادم">
                <label>ملاحظة المعاينة والمراجعة<input aria-label="ملاحظة المعاينة والمراجعة" className={input} value={note} onChange={e => setNote(e.target.value)} /></label>
                <button className={button} disabled={busy || locked || !canSave || blocked || dirty.length > 0 || note.trim().length < 3} onClick={() => run(async () => accept(await controller.preview(note)))}>معاينة الجلسة على الخادم</button>
                <button className={button} disabled={busy || locked || !canReview || blocked || dirty.length > 0 || session.status !== "previewed" || note.trim().length < 3} onClick={() => run(async () => accept(await controller.review(note)))}>مراجعة الجلسة وقفلها</button>
                <button className={button} disabled={busy || dirty.length > 0} onClick={read}>فحص جاهزية المصدر</button>
                {session.preview && dirty.length === 0 && <div data-testid="server-preview"><p>معاينة الخادم: مدين {session.preview.debit_total} · دائن {session.preview.credit_total} · {session.preview.balanced === true ? "متوازن" : "مشكلة مطابقة"}</p><p>حسابات الصفر الصريح: {session.preview.zero_accounts?.length ?? 0}</p><p>مطابقة تقييم المخزون: {session.preview.inventory_reconciliation?.verified === true ? "مكتمل" : "غير مثبتة"}</p></div>}
                {readiness && <div data-testid="server-readiness"><p>جاهزية المصدر: {readiness.source_ready === true ? "مكتمل" : "ناقص"}</p><p>مطابقة التقييم المالي: {readiness.inventory_reconciled === true ? "مكتمل" : "غير مثبتة"}</p><p>اعتماد الكميات الفعلية: {readiness.inventory_physical_approval_verified === true ? "مثبت" : "غير مثبت"}</p><p dir="ltr">Smoke B: {readiness.live_gates?.smoke_b || "UNVERIFIED"}</p><p dir="ltr">ready_for_live_post={String(readiness.ready_for_live_post === true)}</p><p>موانع الخادم: {readiness.blockers?.length ?? "غير معروفة"}</p><ul>{(readiness.blockers || []).map((blocker, index) => <li key={index}>{LABELS[blocker.section_id] || "المراجعة العامة"}: {api.onboardingErrorMessage({ response: { data: { detail: { code: blocker.code } } } })}</li>)}</ul></div>}
                <p>P02 — LOCKED. لا ترحيل أو تفعيل من هذا المعالج.</p>
            </section>
        </>}
    </div>;
}
