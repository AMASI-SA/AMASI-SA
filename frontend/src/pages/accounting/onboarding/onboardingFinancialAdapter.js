// Pure Track A V1 financial projection. Never sends domain drafts or calls a service.
import { scaledDecimal } from "./onboardingDecimal";

export const FINANCIAL_STAGE_SECTIONS = { banks: "banks_cash", providers: "providers", advertising: "providers", employees: "payroll_obligations", suppliers: "suppliers", external_persons: "suppliers", courier_balances: "couriers_cod", drivers: "couriers_cod", inventory: "inventory", prepaid: "equity", obligations: "equity" };
const FIELDS = {
    providers: [["balance", "provider_receivable", "available_to_us"]],
    employees: [["salary_payable", "employee_salary_payable", "owed_by_us"], ["advance", "employee_advance", "available_to_us"], ["custody", "employee_custody", "available_to_us"]],
    suppliers: [["payable", "supplier_payable", "owed_by_us"], ["advance", "supplier_advance", "available_to_us"]],
    external_persons: [["receivable", "customer_receivable", "available_to_us"]],
    drivers: [["cod_receivable", "store_driver_cod_receivable", "available_to_us"], ["fee_payable", "store_driver_fee_payable", "owed_by_us"]],
    courier_balances: [["opening_cod_receivable", "courier_cod_receivable", "available_to_us"], ["opening_payable", "courier_payable", "owed_by_us"]],
};
const TERMS = { prepaid_expense: "available_to_us", accrued_expense: "owed_by_us", other_receivable: "available_to_us", other_payable: "owed_by_us", input_vat: "available_to_us", sales_vat_payable: "owed_by_us" };
const accountList = context => context.financial_accounts || context.entities?.financial_accounts || [];
const has = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
const fingerprint = rows => JSON.stringify(rows);
const unchanged = section => section._financialRows !== undefined && section._financialRows === fingerprint(section.rows || []);
const courierSnapshot = couriers => fingerprint(Object.entries(couriers || {}).map(([id, row]) => [id, ...["opening_cod_receivable", "opening_payable", "original_currency", "fx_rate_to_sar", "fx_at", "fx_source", "fx_evidence_file_id", "evidence_file_id"].map(key => row[key])]));
function monetary(value) {
    if (value === "" || value === undefined || value === null) return null;
    const amount = scaledDecimal(value, 2);
    if (amount === null) throw new Error("onboarding_amount_invalid");
    return amount;
}
function line(row, category, field, meaning, evidenceFileId, identity = {}, currency) {
    const result = { category, ...identity, label: row.label || row.name || "", original_currency: currency || row.original_currency || "SAR" };
    const amount = monetary(row[field]);
    if (amount !== null) {
        result.original_amount = row[field];
        if (amount === 0n || meaning) result.meaning = amount === 0n ? "zero" : meaning;
    }
    result.fx_rate_to_sar = result.original_currency === "SAR" ? "1" : row.fx_rate_to_sar || "";
    for (const key of ["fx_at", "fx_source", "fx_evidence_file_id"]) if (row[key]) result[key] = row[key];
    if (/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?$/.test(result.fx_at || "")) result.fx_at += "+03:00";
    if (row.evidence_file_id || evidenceFileId) result.evidence_file_id = row.evidence_file_id || evidenceFileId;
    return result;
}
function financialLine(row, field, id, allowedTypes, context, evidenceFileId) {
    if (!id) {
        const partial = line(row, "financial_account", field, undefined, evidenceFileId, { financial_account_id: "" });
        if (!row.original_currency) { delete partial.original_currency; delete partial.fx_rate_to_sar; }
        return partial;
    }
    const account = accountList(context).find(a => a.id === id);
    if (!account || !allowedTypes.includes(account.account_type) || !account.currency) throw new Error("onboarding_financial_account_unresolved");
    if (allowedTypes.some(type => ["ad_prepaid_wallet", "ad_payable"].includes(type)) && row.entity_id && account.external_ref !== row.entity_id) throw new Error("onboarding_financial_identity_conflict");
    if (row.original_currency && row.original_currency !== account.currency) throw new Error("onboarding_account_currency_mismatch");
    return line(row, "financial_account", field, ["overdraft", "ad_payable"].includes(account.account_type) ? "owed_by_us" : "available_to_us", evidenceFileId, { financial_account_id: id }, account.currency);
}
function owned(stage, item, context) {
    if (stage === "banks" || stage === "advertising") {
        if (item.category !== "financial_account") return false;
        const account = accountList(context).find(a => a.id === item.financial_account_id);
        // Financial-account stages live in different server sections; unresolved IDs
        // remain editable incomplete facts in that section, never disappear on resume.
        return !account || (stage === "banks" ? ["bank", "cash", "overdraft"] : ["ad_prepaid_wallet", "ad_payable"]).includes(account.account_type);
    }
    if (stage === "inventory") return item.category === "inventory_asset";
    if (stage === "prepaid") return item.category === "prepaid_expense";
    if (stage === "obligations") return has(TERMS, item.category) && item.category !== "prepaid_expense";
    return (FIELDS[stage] || []).some(([, category]) => category === item.category);
}
function project(stage, view, context, evidenceFileId) {
    const section = view.sections?.[stage] || {};
    if (section.status === "not_applicable") {
        if ((section.rows || []).length || (stage === "courier_balances" && Object.keys(view.couriers || {}).length)) throw new Error("onboarding_not_applicable_conflict");
        return [];
    }
    const rows = stage === "courier_balances" ? Object.entries(view.couriers || {}).map(([entity_id, draft]) => ({ ...draft, entity_id })) : section.rows || [];
    if (stage === "courier_balances" && section._financialCourierRows === courierSnapshot(view.couriers) && section._financialLines) return section._financialLines.map(item => ({ ...item }));
    if (unchanged(section) && section._financialLines) return section._financialLines.map(item => ({ ...item }));
    if (section._financialMetadataConflict) throw new Error("onboarding_subaccount_metadata_conflict");
    if (stage === "banks") return rows.map(row => {
        if (row.financial_account_id && row.entity_id && row.financial_account_id !== row.entity_id) throw new Error("onboarding_financial_identity_conflict");
        return financialLine(row, "balance", row.entity_id || row.financial_account_id, ["bank", "cash", "overdraft"], context, evidenceFileId);
    });
    if (stage === "advertising") return rows.flatMap(row => {
        if (row.financial_account_id) {
            const account = accountList(context).find(a => a.id === row.financial_account_id);
            const field = account?.account_type === "ad_payable" ? "payable" : "prepaid_wallet";
            return [financialLine(row, field, row.financial_account_id, ["ad_prepaid_wallet", "ad_payable"], context, evidenceFileId)];
        }
        // IDs must come from explicit financial-account selection, never external_ref/name matching.
        const mappings = [["prepaid_wallet", "prepaid_wallet_account_id", "ad_prepaid_wallet"], ["payable", "payable_account_id", "ad_payable"]];
        return mappings.filter(([field, id]) => has(row, field) || has(row, id)).map(([field, id, type]) => financialLine(row, field, row[id], [type], context, evidenceFileId));
    });
    if (stage === "inventory") {
        if (!has(section, "rows") && section.financial_lines) return section.financial_lines.map(item => ({ ...item }));
        const grouped = new Map();
        for (const row of rows) {
            if (!row.inventory_account_id) continue;
            const amount = monetary(row.opening_total_cost);
            const previous = grouped.get(row.inventory_account_id);
            grouped.set(row.inventory_account_id, { amount: previous?.amount === null || amount === null ? null : (previous?.amount || 0n) + amount });
        }
        return [...grouped].map(([entity_id, { amount }]) => line({ amount: amount === null ? "" : `${amount / 100n}.${String(amount % 100n).padStart(2, "0")}` }, "inventory_asset", "amount", "available_to_us", evidenceFileId, { entity_id })).concat(rows.filter(row => !row.inventory_account_id).map(row => line(row, "inventory_asset", "opening_total_cost", "available_to_us", evidenceFileId, { entity_id: "" })));
    }
    if (stage === "prepaid" || stage === "obligations") return rows.map(row => {
        if (row.classification && (!has(TERMS, row.classification) || (stage === "prepaid" && row.classification !== "prepaid_expense") || (stage === "obligations" && row.classification === "prepaid_expense"))) throw new Error("onboarding_classification_invalid");
        return line(row, row.classification || "", "amount", TERMS[row.classification], evidenceFileId, { entity_id: row.entity_id || "" });
    });
    return rows.flatMap(row => FIELDS[stage].filter(([field]) => stage !== "suppliers" || field !== "advance" || has(row, field)).map(([field, category, meaning]) => line(row, category, field, meaning, evidenceFileId, { entity_id: row.entity_id || "" })));
}

// Full replacements must include every loaded sibling plus saved, unloaded siblings.
// Caller owns status/evidence validation and supplies evidence IDs returned by the evidence service.
export function buildFinancialSection(stageId, view, savedSection = {}, context = {}, options = {}) {
    const sectionId = FINANCIAL_STAGE_SECTIONS[stageId];
    if (!sectionId) throw new Error("onboarding_domain_local_stage");
    const saved = savedSection.data || savedSection;
    const data = { lines: [...(saved.lines || [])] };
    for (const key of ["provider_bindings", "inventory_valuation", "fee_policy_ids", "prepaid_selection_ids", "typed_fact_ids"]) if (has(saved, key)) data[key] = saved[key];
    const stages = Object.keys(FINANCIAL_STAGE_SECTIONS).filter(stage => FINANCIAL_STAGE_SECTIONS[stage] === sectionId && (stage === stageId || has(view.sections?.[stage], "rows") || (stage === "courier_balances" && has(view, "couriers"))));
    for (const stage of stages) {
        const evidence = view.sections?.[stage]?.evidence_file_id || options.evidenceFileId;
        data.lines = [...data.lines.filter(item => !owned(stage, item, context)), ...project(stage, view, context, evidence)];
        if (stage === "providers") data.provider_bindings = unchanged(view.sections?.providers || {}) && view.sections.providers._providerBindings ? view.sections.providers._providerBindings.map(binding => ({ ...binding })) : (view.sections?.providers?.rows || []).map(row => ({ provider: row.entity_id || "", bank_account_id: row.settlement_bank_id || "", evidence_file_id: row.binding_evidence_file_id || row.evidence_file_id || evidence || "" }));
    }
    // One uploaded section artifact is authoritative for every sibling line and
    // binding, including unchanged restored facts. FX evidence is independent.
    if (options.evidenceFileId) {
        data.lines = data.lines.map(item => ({ ...item, evidence_file_id: options.evidenceFileId }));
        if (data.provider_bindings) data.provider_bindings = data.provider_bindings.map(binding => ({ ...binding, evidence_file_id: options.evidenceFileId }));
    }
    if (sectionId === "inventory") {
        if (fingerprint(data.lines) !== fingerprint(saved.lines || []) || (options.evidenceFileId && options.evidenceFileId !== saved.inventory_valuation?.evidence_file_id)) delete data.inventory_valuation;
        if (options.evidenceFileId && /^[a-f0-9]{64}$/.test(options.manifestHash || "")) {
            const values = data.lines.map(item => monetary(item.original_amount));
            if (values.length && values.every(amount => amount !== null) && data.lines.every(item => item.entity_id && item.original_currency === "SAR")) {
                const total = values.reduce((sum, amount) => sum + amount, 0n);
                const accountTotals = {};
                for (const item of data.lines) {
                    if (has(accountTotals, item.entity_id)) throw new Error("onboarding_duplicate_inventory_account");
                    Object.defineProperty(accountTotals, item.entity_id, { value: item.original_amount, enumerable: true });
                }
                data.inventory_valuation = { total_sar: `${total / 100n}.${String(total % 100n).padStart(2, "0")}`, account_totals: accountTotals, evidence_file_id: options.evidenceFileId, manifest_hash: options.manifestHash };
            }
        }
    }
    return { sectionId, data };
}

// Restore only financial facts. Quantities, courier terms and fee drafts have no V1 storage contract.
export function restoreFinancialSession(session, previousView = {}, context = {}) {
    const view = { ...previousView, sections: { ...(previousView.sections || {}) }, couriers: { ...(previousView.couriers || {}) } };
    for (const [stage, sectionId] of Object.entries(FINANCIAL_STAGE_SECTIONS)) {
        const saved = session.sections?.[sectionId];
        if (!saved) continue;
        const items = (saved.data?.lines || []).filter(item => owned(stage, item, context));
        const metadata = { explicit_zero: items.length > 0 && items.every(item => item.meaning === "zero"), status: saved.status, evidence_file_id: saved.evidence_file_id || "", evidence_ref: saved.evidence_file_id || "", not_applicable_reason: saved.reason || "" };
        const previous = view.sections[stage] || {};
        if (stage === "inventory") {
            view.sections[stage] = { ...previous, ...metadata, financial_lines: items, inventory_valuation: saved.data?.inventory_valuation };
            continue;
        }
        const rows = new Map();
        let metadataConflict = false;
        for (const item of items) {
            const id = item.financial_account_id || item.entity_id || "";
            const rowKey = id || `incomplete-${items.indexOf(item)}`;
            let row = rows.get(rowKey);
            const financialMetadata = { original_currency: item.original_currency, fx_rate_to_sar: item.fx_rate_to_sar, fx_at: item.fx_at, fx_source: item.fx_source, fx_evidence_file_id: item.fx_evidence_file_id, evidence_file_id: item.evidence_file_id, evidence_ref: item.evidence_file_id || "" };
            if (!row) {
                row = { entity_id: id, label: item.label, ...financialMetadata };
                rows.set(rowKey, row);
            } else if (["original_currency", "fx_rate_to_sar", "fx_at", "fx_source", "fx_evidence_file_id", "evidence_file_id"].some(key => row[key] !== item[key])) {
                // The single-row UI cannot faithfully represent distinct snapshots per subaccount.
                metadataConflict = true;
            }
            if (stage === "banks") { row.financial_account_id = id; row.balance = item.original_amount ?? ""; }
            else if (stage === "advertising") {
                row.entity_id = ""; // A financial account ID is not an ad profile identity.
                const account = accountList(context).find(a => a.id === id);
                if (account) {
                    const payable = account.account_type === "ad_payable";
                    row[payable ? "payable" : "prepaid_wallet"] = item.original_amount ?? "";
                    row[payable ? "payable_account_id" : "prepaid_wallet_account_id"] = id;
                } else { row.financial_account_id = id; row._unresolved_account = true; row.original_amount = item.original_amount; }
            } else if (stage === "prepaid" || stage === "obligations") {
                // One explicit account may have multiple classifications; preserve each as its own row.
                rows.delete(id);
                rows.set(`${id}:${item.category}`, { ...row, classification: item.category, name: item.label || "", amount: item.original_amount ?? "" });
            } else {
                const field = FIELDS[stage].find(([, category]) => category === item.category)?.[0];
                if (field) row[field] = item.original_amount ?? "";
            }
            if (stage === "providers") {
                const binding = (saved.data?.provider_bindings || []).find(b => b.provider === id);
                if (binding) { row.settlement_bank_id = binding.bank_account_id; row.binding_evidence_file_id = binding.evidence_file_id; }
            }
        }
        if (stage === "courier_balances") {
            for (const id of Object.keys(view.couriers)) {
                const { opening_cod_receivable, opening_payable, ...terms } = view.couriers[id];
                view.couriers[id] = terms;
            }
            for (const [id, row] of rows) view.couriers[id] = { ...(view.couriers[id] || {}), ...row };
            view.sections[stage] = { ...previous, ...metadata, _financialLines: items.map(item => ({ ...item })), _financialCourierRows: courierSnapshot(view.couriers), _financialMetadataConflict: metadataConflict };
        } else {
            const restoredRows = [...rows.values()];
            view.sections[stage] = { ...previous, ...metadata, rows: restoredRows, _financialRows: fingerprint(restoredRows), _financialLines: items.map(item => ({ ...item })), _financialMetadataConflict: metadataConflict };
            if (stage === "providers") view.sections[stage]._providerBindings = (saved.data?.provider_bindings || []).map(binding => ({ ...binding }));
        }
    }
    if (session.cutover) {
        const parsed = Date.parse(session.cutover.cutover_at);
        const local = Number.isFinite(parsed) ? new Date(parsed + 3 * 60 * 60 * 1000).toISOString().slice(0, 19) : "";
        view.sections.cutover = { ...(view.sections.cutover || {}), status: local && session.cutover.cutover_evidence_file_id ? "complete" : "incomplete", cutover_at: local, evidence_file_id: session.cutover.cutover_evidence_file_id, evidence_ref: session.cutover.cutover_evidence_file_id };
    }
    return view;
}
