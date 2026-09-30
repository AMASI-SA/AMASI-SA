import { buildFinancialSection, restoreFinancialSession } from "./onboardingFinancialAdapter";
const context = { financial_accounts: [
    { id: "bank", account_type: "bank", currency: "SAR" },
    { id: "overdraft", account_type: "overdraft", currency: "SAR" },
    { id: "wallet", account_type: "ad_prepaid_wallet", currency: "USD" },
    { id: "debt", account_type: "ad_payable", currency: "USD" },
] };
const options = { evidenceFileId: "server-file" };

test("full supplier replacement rebuilds loaded siblings but retains unloaded external persons", () => {
    const external = { category: "customer_receivable", entity_id: "person", original_amount: "7" };
    const saved = { data: { lines: [external, { category: "supplier_payable", entity_id: "old" }] } };
    const view = { sections: { suppliers: { rows: [{ entity_id: "supplier", payable: "12.00", advance: "3.00" }] } } };
    const result = buildFinancialSection("suppliers", view, saved, context, options);
    expect(result.sectionId).toBe("suppliers");
    expect(result.data.lines).toEqual([external, expect.objectContaining({ category: "supplier_payable", entity_id: "supplier", original_amount: "12.00", meaning: "owed_by_us" }), expect.objectContaining({ category: "supplier_advance", original_amount: "3.00", meaning: "available_to_us" })]);
    view.sections.external_persons = { rows: [{ entity_id: "person", receivable: "9" }] };
    expect(buildFinancialSection("suppliers", view, saved, context, options).data.lines.find(l => l.category === "customer_receivable").original_amount).toBe("9");
    expect(saved.data.lines).toHaveLength(2);
});

test("restore roundtrip retains financial siblings and preserves domain-local drafts without inventing quantities", () => {
    const provider = { category: "provider_receivable", entity_id: "tabby", label: "Tabby", meaning: "zero", original_amount: "0", original_currency: "SAR", fx_rate_to_sar: "1", evidence_file_id: "server-file" };
    const session = { sections: { providers: { status: "incomplete", evidence_file_id: "server-file", data: { lines: [provider], provider_bindings: [{ provider: "tabby", bank_account_id: "bank", evidence_file_id: "server-file" }] } }, inventory: { status: "incomplete", data: { lines: [{ category: "inventory_asset", entity_id: "asset", original_amount: "12" }] } } } };
    const restored = restoreFinancialSession(session, { couriers: { courier: { shipping_cost: "10" } }, sections: { payment_fees: { rows: [{ fixed_fee_per_order: "2" }] } } }, context);
    expect(restored.couriers.courier.shipping_cost).toBe("10");
    expect(restored.sections.payment_fees.rows).toHaveLength(1);
    expect(restored.sections.inventory).not.toHaveProperty("rows");
    expect(buildFinancialSection("providers", restored, session.sections.providers, context, options).data.lines).toEqual([provider]);
    expect(buildFinancialSection("inventory", restored, session.sections.inventory, context).data.lines[0].original_amount).toBe("12");
});

test("missing amount never becomes zero; explicit zero and overdraft use exact account identity", () => {
    const result = buildFinancialSection("banks", { sections: { banks: { rows: [{ entity_id: "bank", balance: "" }, { entity_id: "overdraft", balance: "12" }] } } }, {}, context, options);
    expect(result.data.lines[0]).not.toHaveProperty("original_amount");
    expect(result.data.lines[0]).not.toHaveProperty("meaning");
    expect(result.data.lines[1]).toMatchObject({ financial_account_id: "overdraft", meaning: "owed_by_us", original_currency: "SAR", fx_rate_to_sar: "1", evidence_file_id: "server-file" });
    expect(buildFinancialSection("banks", { sections: { banks: { rows: [{ entity_id: "bank", balance: "0.00" }] } } }, {}, context).data.lines[0].meaning).toBe("zero");
    expect(() => buildFinancialSection("banks", { sections: { banks: { rows: [{ entity_id: "bank", balance: "-1" }] } } }, {}, context)).toThrow("amount_invalid");
});

test("providers preserve ads and map proposed settlement binding without domain fields", () => {
    const ad = { category: "financial_account", financial_account_id: "wallet", original_amount: "50" };
    const result = buildFinancialSection("providers", { sections: { providers: { rows: [{ entity_id: "tabby", balance: "0", settlement_bank_id: "bank", evidence_ref: "not-a-file-id" }] } } }, { data: { lines: [ad] } }, context, options);
    expect(result.data.lines[0]).toEqual(ad);
    expect(result.data.provider_bindings).toEqual([{ provider: "tabby", bank_account_id: "bank", evidence_file_id: "server-file" }]);
    expect(JSON.stringify(result)).not.toContain("not-a-file-id");
});

test("ads require selected financial IDs and preserve account currency and FX", () => {
    const row = { entity_id: "profile", prepaid_wallet: "15", payable: "2", prepaid_wallet_account_id: "wallet", payable_account_id: "debt", original_currency: "USD", fx_rate_to_sar: "3.75", fx_at: "2026-10-01T00:00:00+03:00", fx_source: "fixture" };
    const run = r => buildFinancialSection("advertising", { sections: { advertising: { rows: [r] } } }, {}, context, options);
    expect(run(row).sectionId).toBe("providers");
    expect(run(row).data.lines.map(l => [l.financial_account_id, l.meaning, l.original_currency])).toEqual([["wallet", "available_to_us", "USD"], ["debt", "owed_by_us", "USD"]]);
    expect(run({ ...row, prepaid_wallet_account_id: undefined }).data.lines[0]).toMatchObject({ financial_account_id: "" });
    expect(run({ ...row, prepaid_wallet_account_id: undefined }).data.lines[0]).not.toHaveProperty("meaning");
    expect(() => run({ ...row, original_currency: "SAR" })).toThrow("currency_mismatch");
});

test("employee and courier balances preserve independent meanings and domain terms never leak", () => {
    expect(buildFinancialSection("employees", { sections: { employees: { rows: [{ entity_id: "employee", salary_payable: "10", advance: "0", custody: "3" }] } } }, {}, context, options).data.lines.map(l => l.meaning)).toEqual(["owed_by_us", "zero", "available_to_us"]);
    const view = { sections: { courier_balances: {}, drivers: { rows: [{ entity_id: "driver", cod_receivable: "4", fee_payable: "5" }] } }, couriers: { courier: { opening_cod_receivable: "7", opening_payable: "0", shipping_cost: "50", cod_fee_tiers: [] } } };
    const result = buildFinancialSection("courier_balances", view, {}, context, options);
    expect(result.data.lines).toHaveLength(4);
    expect(JSON.stringify(result)).not.toMatch(/shipping_cost|cod_fee_tiers/);
});

test("inventory sum is exact and needs verified evidence plus manifest for valuation summary", () => {
    const view = { sections: { inventory: { rows: [{ inventory_account_id: "asset", opening_total_cost: "0.10", allocations: [{ quantity: "1" }] }, { inventory_account_id: "asset", opening_total_cost: "0.20" }] } } };
    const run = opts => buildFinancialSection("inventory", view, {}, context, opts).data;
    expect(run(options).lines[0].original_amount).toBe("0.30");
    expect(run(options)).not.toHaveProperty("inventory_valuation");
    expect(run({ ...options, manifestHash: "a".repeat(64) }).inventory_valuation).toEqual({ total_sar: "0.30", account_totals: { asset: "0.30" }, evidence_file_id: "server-file", manifest_hash: "a".repeat(64) });
    expect(JSON.stringify(run(options))).not.toContain("allocations");
});

test("prepaid and accrual classification stays distinct and domain-local stages reject projection", () => {
    const result = buildFinancialSection("prepaid", { sections: { prepaid: { rows: [{ classification: "prepaid_expense", entity_id: "asset", amount: "20" }] }, obligations: { rows: [{ classification: "accrued_expense", entity_id: "liability", amount: "30" }] } } }, {}, context, options);
    expect(result.sectionId).toBe("equity");
    expect(result.data.lines.map(l => [l.category, l.meaning])).toEqual([["prepaid_expense", "available_to_us"], ["accrued_expense", "owed_by_us"]]);
    expect(() => buildFinancialSection("payment_fees", {})).toThrow("domain_local");
    expect(() => buildFinancialSection("suppliers", { sections: { suppliers: { status: "not_applicable", rows: [{ payable: "4" }] } } })).toThrow("not_applicable_conflict");
});

test("partial banks inventory and terms save unknown facts without manufacturing zero", () => {
    for (const [stage, rows] of [["banks", [{ entity_id: "", balance: "" }]], ["inventory", [{ opening_total_cost: "" }]], ["prepaid", [{}]]]) {
        const data = buildFinancialSection(stage, { sections: { [stage]: { rows } } }, {}, context, options).data;
        expect(data.lines).toHaveLength(1);
        expect(data.lines[0]).not.toHaveProperty("original_amount");
        expect(data.lines[0]).not.toHaveProperty("meaning");
    }
    expect(buildFinancialSection("inventory", { sections: { inventory: { rows: [] } } }, {}, context, { ...options, manifestHash: "a".repeat(64) }).data).not.toHaveProperty("inventory_valuation");
});

test("untouched restored subaccounts preserve distinct evidence and exact partial schema", () => {
    const lines = [{ category: "employee_salary_payable", entity_id: "e", original_amount: "1", evidence_file_id: "salary" }, { category: "employee_advance", entity_id: "e", original_amount: "0", evidence_file_id: "advance" }];
    const saved = { status: "incomplete", data: { lines } };
    const view = restoreFinancialSession({ sections: { payroll_obligations: saved } }, {}, context);
    expect(buildFinancialSection("employees", view, saved, context).data.lines).toEqual(lines);
    view.sections.employees.rows[0].salary_payable = "3";
    expect(() => buildFinancialSection("employees", view, saved, context)).toThrow("subaccount_metadata_conflict");
});

test("unresolved resumed financial identity remains visible and exact", () => {
    const saved = { status: "incomplete", data: { lines: [{ category: "financial_account", financial_account_id: "unresolved", original_amount: "20" }] } };
    const view = restoreFinancialSession({ sections: { banks_cash: saved } }, {}, context);
    expect(view.sections.banks.rows[0].entity_id).toBe("unresolved");
    expect(buildFinancialSection("banks", view, saved, context).data.lines).toEqual(saved.data.lines);
});

test("conflicting bank IDs fail closed and FX local datetime uses Riyadh offset", () => {
    expect(() => buildFinancialSection("banks", { sections: { banks: { rows: [{ entity_id: "bank", financial_account_id: "overdraft", balance: "4" }] } } }, {}, context)).toThrow("identity_conflict");
    const result = buildFinancialSection("advertising", { sections: { advertising: { rows: [{ financial_account_id: "wallet", prepaid_wallet: "5", fx_rate_to_sar: "3.75", fx_at: "2026-10-01T00:00", fx_source: "fixture" }] } } }, {}, context);
    expect(result.data.lines[0].fx_at).toBe("2026-10-01T00:00+03:00");
    expect(restoreFinancialSession({ cutover: { cutover_at: "2026-09-30T21:00:00Z" } }).sections.cutover.cutover_at).toBe("2026-10-01T00:00:00");
});

test("unchanged inventory manifest survives resume and account changes invalidate prior valuation", () => {
    const saved = { status: "incomplete", data: { lines: [{ category: "inventory_asset", entity_id: "asset", original_amount: "2.00", original_currency: "SAR" }], inventory_valuation: { total_sar: "2.00", account_totals: { asset: "2.00" }, evidence_file_id: "server-file", manifest_hash: "a".repeat(64) } } };
    const view = restoreFinancialSession({ sections: { inventory: saved } }, {}, context);
    expect(buildFinancialSection("inventory", view, saved, context).data.inventory_valuation).toEqual(saved.data.inventory_valuation);
    view.sections.inventory.rows = [{ inventory_account_id: "other-asset", opening_total_cost: "2.00" }];
    expect(buildFinancialSection("inventory", view, saved, context).data).not.toHaveProperty("inventory_valuation");
    expect(buildFinancialSection("inventory", view, saved, context, { ...options, manifestHash: "b".repeat(64) }).data.inventory_valuation.account_totals).toEqual({ "other-asset": "2.00" });
});

test("external exact identity preserved and contact data never enters financial line", () => {
    const result = buildFinancialSection("external_persons", { sections: { external_persons: { rows: [{ entity_id: "person-exact-id", receivable: "0", phone: "0500000000", notes: "private" }] } } }, {}, context, options);
    expect(result.data.lines[0]).toMatchObject({ entity_id: "person-exact-id", meaning: "zero" });
    expect(JSON.stringify(result)).not.toMatch(/phone|notes/);
});

test("placeholder sibling sections do not delete saved lines and courier terms edits preserve financial schema", () => {
    const external = { category: "customer_receivable", entity_id: "person", original_amount: "5" };
    expect(buildFinancialSection("suppliers", { sections: { suppliers: { rows: [] }, external_persons: { status: "incomplete" } } }, { data: { lines: [external] } }, context).data.lines).toEqual([external]);
    const lines = [{ category: "courier_cod_receivable", entity_id: "courier", original_amount: "2" }];
    const saved = { status: "incomplete", data: { lines } };
    const restored = restoreFinancialSession({ sections: { couriers_cod: saved } }, {}, context);
    restored.couriers.courier.shipping_cost = "20";
    expect(buildFinancialSection("courier_balances", restored, saved, context).data.lines).toEqual(lines);
});

test("restored advertising never impersonates a profile with financial ID or external_ref", () => {
    const saved = { status: "incomplete", data: { lines: [{ category: "financial_account", financial_account_id: "wallet", original_amount: "10", original_currency: "USD" }] } };
    const linkedContext = { ...context, financial_accounts: context.financial_accounts.map(a => ({ ...a, external_ref: "looks-like-profile" })), entities: { ad_accounts: [{ id: "looks-like-profile" }] } };
    const view = restoreFinancialSession({ sections: { providers: saved } }, {}, linkedContext);
    expect(view.sections.advertising.rows[0]).toMatchObject({ entity_id: "", prepaid_wallet_account_id: "wallet", prepaid_wallet: "10" });
    expect(buildFinancialSection("advertising", view, saved, linkedContext).data.lines).toEqual(saved.data.lines);
});
