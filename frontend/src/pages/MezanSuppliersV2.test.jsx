import { act } from "react";
import { createRoot } from "react-dom/client";
import { createMezanSupplier, loadMezanSupplierFinancials, loadMezanSuppliersWorkspace, loadOperationalSupplierAccounts } from "../services/mezanSuppliersV2";
import { renderToStaticMarkup } from "react-dom/server";
import { useOptionalAuth } from "../context/AuthContext";

let mockSupplierQuery = '';

jest.mock("react-router-dom", () => ({
    Link: ({ to, children }) => <a href={to}>{children}</a>,
    useSearchParams: () => [new URLSearchParams(mockSupplierQuery), jest.fn()],
}));

jest.mock("../context/AuthContext", () => ({ useOptionalAuth: jest.fn() }));

jest.mock("../services/mezanSuppliersV2", () => ({
    createMezanSupplier: jest.fn(),
    loadMezanSupplierFinancials: jest.fn(() => new Promise(() => {})),
    loadMezanSuppliersWorkspace: jest.fn(() => new Promise(() => {})),
    loadOperationalSupplierAccounts: jest.fn(),
    updateMezanSupplier: jest.fn(),
}));

jest.mock("../services/supplierReceiving", () => ({
    downloadSupplierReceivingInvoicePdf: jest.fn(),
}));

import MezanSuppliersV2, {
    SupplierEditor,
    SupplierFinancialDetail,
    formatSupplierHalalas,
    supplierFormFromRow,
    supplierMatchesQuery,
} from "./MezanSuppliersV2";

const pickerCategories = [{ id: "clothes", name: "ملابس" }, { id: "plated", name: "مطليات" }];
const pickerServices = [
    { id: "embroidery", name: "تطريز", category_ids: ["clothes"] },
    { id: "engraving", name: "حفر", category_ids: ["plated"] },
    { id: "shared", name: "تغليف", category_ids: ["clothes", "plated"] },
];

beforeEach(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    mockSupplierQuery = '';
    useOptionalAuth.mockReturnValue({ user: { id: 'owner', role: 'owner' } });
    loadMezanSuppliersWorkspace.mockReset();
    loadMezanSupplierFinancials.mockReset();
    loadOperationalSupplierAccounts.mockReset();
    loadOperationalSupplierAccounts.mockResolvedValue({ status: "draft", active: false, authority: 'operational_balance_report' });
});

afterEach(() => { delete document.visibilityState; });

async function withEditor(test) {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    createMezanSupplier.mockClear();
    createMezanSupplier.mockResolvedValue({ ok: true });
    try {
        await act(async () => root.render(<SupplierEditor supplier={{ company_name: "مورد" }} categories={pickerCategories} services={pickerServices} onSaved={jest.fn().mockResolvedValue(undefined)} onClose={jest.fn()} />));
        const click = async (text) => act(async () => [...container.querySelectorAll("label")].find((label) => label.textContent === text || label.textContent.startsWith(text)).querySelector("input").click());
        await test(container, click);
    } finally {
        await act(async () => root.unmount());
        container.remove();
    }
}

test("supplier can be saved with multiple categories and zero services", async () => withEditor(async (container, click) => {
    expect(container.querySelector('[type="submit"]').disabled).toBe(true);
    await click("ملابس");
    await click("مطليات");
    expect(container.querySelector('[type="submit"]').disabled).toBe(false);
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(createMezanSupplier).toHaveBeenCalledWith(expect.objectContaining({ category_ids: ["clothes", "plated"], service_ids: [] }));
}));

test("service choices follow categories and removing one retains shared services only", async () => withEditor(async (container, click) => {
    await click("ملابس");
    const picker = () => container.querySelector('[data-testid="mezan-supplier-service-picker"]');
    expect(picker().textContent).toContain("تطريز");
    expect(picker().textContent).not.toContain("حفر");
    await click("تطريز");
    await click("تغليف");
    await click("مطليات");
    await click("ملابس");
    expect(picker().textContent).not.toContain("تطريز");
    expect(picker().textContent).toContain("حفر");
    await act(async () => container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    expect(createMezanSupplier).toHaveBeenCalledWith(expect.objectContaining({ category_ids: ["plated"], service_ids: ["shared"] }));
}));

test("add supplier opens with unavailable financial data without recovery reload", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    loadMezanSuppliersWorkspace.mockResolvedValue({ suppliers: [], categories: pickerCategories, services: pickerServices, permissions: { can_manage: true }, summary: {} });
    loadMezanSupplierFinancials.mockResolvedValue({ financial_status: "not_ready", summary: {} });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => root.render(<MezanSuppliersV2 />));
        await act(async () => container.querySelector('[data-testid="mezan-supplier-add-button"]').click());
        expect(container.querySelector('[data-testid="mezan-supplier-editor"]')).not.toBeNull();
    } finally {
        await act(async () => root.unmount());
        container.remove();
    }
});


test("Mezan 2 supplier page states the independent governed supplier contract", () => {
    const markup = renderToStaticMarkup(<MezanSuppliersV2 />);

    expect(markup).toContain('data-testid="mezan-suppliers-v2-page"');
    expect(markup).toContain("الموردون");
    expect(markup).toContain("موردون وفواتير ومديونيات ميزان 2 فقط");
    expect(markup).toContain("1 · الاستلام");
    expect(markup).toContain("2 · الفاتورة");
    expect(markup).toContain("3 · حساب المورد");
    expect(markup).toContain("هوية المورد من سجل ميزان 2 فقط");
    expect(markup).toContain("اطلب عرض الحسابات");
    expect(markup).toContain("عرض حسابات الموردين");
    expect(markup).not.toContain("إجمالي ديون الموردين");
    expect(markup).toContain('data-testid="mezan-supplier-add-button"');
});


test("supplier editor model preserves only explicit Mezan 2 service ids", () => {
    expect(supplierFormFromRow({
        company_name: "مورد الحفر",
        service_ids: ["engrave"],
        status: "active",
    })).toMatchObject({
        company_name: "مورد الحفر",
        service_ids: ["engrave"],
        status: "active",
    });
    expect(supplierFormFromRow(null).service_ids).toEqual([]);
});


test("supplier search includes linked service names", () => {
    const supplier = {
        company_name: "مورد أماسي",
        service_links: [{ service_name: "حفر الاسم" }],
    };
    expect(supplierMatchesQuery(supplier, "حفر")).toBe(true);
    expect(supplierMatchesQuery(supplier, "طباعة")).toBe(false);
});


test("supplier financial detail separates eligible balances from historical invoices", () => {
    const markup = renderToStaticMarkup(
        <SupplierFinancialDetail
            supplier={{
                id: "supplier-v2-1",
                company_name: "مورد ميزان 2",
                financial: {
                    outstanding_halalas: 11_000,
                    invoiced_halalas: 11_000,
                    paid_halalas: 0,
                    real_invoice_count: 1,
                    experiment_invoice_count: 1,
                },
            }}
            invoices={[
                {
                    id: "real-1",
                    supplier_id: "supplier-v2-1",
                    invoice_number: "SI-001",
                    total_halalas: 11_000,
                    piece_count: 2,
                    lines: [],
                    financial_eligible: true,
                    paid_halalas: 3000,
                    outstanding_halalas: 8000,
                    payment_status: "partial",
                },
                {
                    id: "experiment-1",
                    supplier_id: "supplier-v2-1",
                    invoice_number: "SI-TEST-001",
                    total_halalas: 11_000,
                    piece_count: 2,
                    lines: [],
                    financial_eligible: false,
                    exclusion_reason: "history_only",
                },
            ]}
            timeline={[{
                id: "ledger-1",
                kind: "invoice",
                amount_halalas: 11_000,
                notes: "فاتورة مورد ميزان 2",
            }]}
            workspace={{ financial_status: "available" }}
            downloadBusy=""
            onDownload={jest.fn()}
            onClose={jest.fn()}
        />,
    );

    expect(markup).toContain('data-testid="mezan-supplier-real-invoice"');
    expect(markup).toContain('data-testid="mezan-supplier-history-invoice"');
    expect(markup).toContain("مسددة جزئيًا");
    expect(markup).toContain("80.00");
    expect(markup).toContain("لا ينشئ رصيدًا مستحقًا");
    expect(markup).toContain("دون مقاصة تلقائية");
    expect(formatSupplierHalalas(null)).toBe("غير متاح");
    expect(formatSupplierHalalas(11_000)).toBe("110.00");
});


test("financial reload failure clears stale balances and removes unverified payment controls", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const supplier = { id: "supplier-v2", company_name: "مورد ميزان", status: "active" };
    loadMezanSuppliersWorkspace.mockResolvedValue({ suppliers: [supplier], services: [], summary: {} });
    loadMezanSupplierFinancials.mockResolvedValueOnce({
        financial_status: "available", payment_available: true,
        payment_accounts: [{ id: "bank-v2", name: "بنك" }],
        suppliers: [{ ...supplier, financial: { outstanding_halalas: 100000, advance_halalas: 0 } }],
        invoices: [], timeline: [], summary: {},
    }).mockRejectedValueOnce(new Error("فشل تحديث الحساب المالي"));
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => root.render(<MezanSuppliersV2 />));
        await act(async () => container.querySelector('[data-testid="mezan-supplier-financial-open-supplier-v2"]').click());
        await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "دفع مبلغ للمورد").click());
        expect(container.querySelector('form button[type="submit"]').disabled).toBe(false);
        expect(container.textContent).toContain("1,000.00");
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        expect(container.textContent).toContain("فشل تحديث الحساب المالي");
        expect(container.textContent).not.toContain("1,000.00");
        expect(container.querySelector('form button[type="submit"]')).toBeNull();
        expect(container.querySelector('[data-testid="mezan-supplier-financial-detail"]')).toBeNull();
    } finally {
        await act(async () => root.unmount());
        container.remove();
    }
});

const operationalWorkspace = () => ({
    active: true, status: "active", authority: "operational_balance_report",
    started_at: "2026-11-01T00:00:00+03:00", as_of: "2026-11-01T01:00:00+03:00",
    accounts: [{ supplier_id: "supplier-v2", currency: "SAR", outstanding_payable: "135.00", outstanding_receivable: "0.00" }],
    openings: [{ id: "opening", supplier_id: "supplier-v2", currency: "SAR", direction: "payable", amount: "100.00", settled: "0.00", outstanding: "100.00" }],
    invoices: [{ id: "new-invoice", supplier_id: "supplier-v2", currency: "SAR", invoice_number: "TEST-SI-001", approved_at: "2026-11-01T00:15:00+03:00", gross_amount: "60.00", returned_amount: "5.00", confirmed: "55.00", settled: "20.00", outstanding: "35.00" }],
    summaries: { SAR: { payable: "135.00", receivable: "0.00", settled: "20.00", invoice_count: 1 } },
    permissions: { record_movements: true },
});

async function withOperationalPage(test) {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const supplier = { id: "supplier-v2", company_name: "مورد تجريبي", status: "active" };
    loadMezanSuppliersWorkspace.mockResolvedValue({ suppliers: [supplier], categories: pickerCategories, services: [], summary: {}, permissions: { can_manage: true } });
    loadMezanSupplierFinancials.mockResolvedValue({ financial_status: "not_ready", payment_available: false, summary: {} });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => root.render(<MezanSuppliersV2 />));
        await test(container, root);
    } finally {
        await act(async () => root.unmount());
        container.remove();
    }
}

test('initial supplier browsing loads administrative data without calculating any financial workspace', async () => {
    await withOperationalPage(async container => {
        expect(container.textContent).toContain('مورد تجريبي');
        expect(loadMezanSuppliersWorkspace).toHaveBeenCalledTimes(1);
        expect(loadOperationalSupplierAccounts).not.toHaveBeenCalled();
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
        expect(container.textContent).toContain('عرض حسابات الموردين');
        expect(container.textContent).not.toContain('إجمالي ديون الموردين');
        expect(container.querySelector('[data-testid="operational-supplier-card"]')).toBeNull();
    });
});

test('an unknown operational authority cannot start a native financial fallback', async () => {
    loadOperationalSupplierAccounts.mockRejectedValue(Object.assign(new Error('لا توجد صلاحية للحسابات'), { status: 403 }));
    await withOperationalPage(async container => {
        await act(async () => container.querySelector('[data-testid="mezan-supplier-financial-open-supplier-v2"]').click());
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
        expect(container.querySelector('[data-testid="mezan-supplier-financial-detail"]')).toBeNull();
        expect(container.querySelector('[role="alert"]')).not.toBeNull();
    });
});

test("active operational supplier invoices appear even when native GL is not ready", async () => {
    loadOperationalSupplierAccounts.mockResolvedValue(operationalWorkspace());
    await withOperationalPage(async container => {
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        expect(container.querySelector('[data-testid="operational-supplier-summary"]')).not.toBeNull();
        expect(container.textContent).toContain("135.00");
        expect(container.textContent).not.toContain("إجمالي ديون الموردين");
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
        await act(async () => container.querySelector('[data-testid="mezan-supplier-financial-open-supplier-v2"]').click());
        const detail = container.querySelector('[data-testid="operational-supplier-detail"]');
        expect(detail.textContent).toContain("TEST-SI-001");
        expect(detail.textContent).toContain("الرصيد الافتتاحي");
        expect(detail.textContent).toContain("100.00");
        expect(detail.textContent).toContain("35.00");
        expect(container.querySelector('[data-testid="mezan-supplier-financial-detail"]')).toBeNull();
        expect(detail.querySelector('a[href="/operational-balances"]')).not.toBeNull();
        expect(detail.querySelector("form")).toBeNull();
        expect(detail.textContent).not.toContain("دفع مبلغ للمورد");
    });
});

test("operational failure clears stale amounts and does not enable native payment controls", async () => {
    loadOperationalSupplierAccounts.mockResolvedValueOnce(operationalWorkspace()).mockRejectedValueOnce(new Error("تعذّر تحديث الحساب التشغيلي"));
    await withOperationalPage(async container => {
        await act(async () => container.querySelector('[data-testid="mezan-supplier-financial-open-supplier-v2"]').click());
        expect(container.textContent).toContain("135.00");
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        expect(container.textContent).not.toContain("135.00");
        expect(container.textContent).not.toContain("TEST-SI-001");
        expect(container.querySelector('[data-testid="operational-supplier-detail"]')).toBeNull();
        expect(container.querySelector('[data-testid="mezan-supplier-financial-detail"]')).toBeNull();
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
        expect(container.querySelector('[role="alert"]').textContent).toContain('تعذّر تحديث الحساب التشغيلي');
    });
});

test('administrative refresh never recalculates already opened supplier accounts', async () => {
    loadOperationalSupplierAccounts.mockResolvedValue(operationalWorkspace());
    await withOperationalPage(async container => {
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        expect(loadOperationalSupplierAccounts).toHaveBeenCalledTimes(1);
        await act(async () => [...container.querySelectorAll('button')].find(button => button.textContent === 'تحديث بيانات الموردين').click());
        expect(loadMezanSuppliersWorkspace).toHaveBeenCalledTimes(2);
        expect(loadOperationalSupplierAccounts).toHaveBeenCalledTimes(1);
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
    });
});

test('saving administrative supplier details does not reload the financial accounts', async () => {
    loadOperationalSupplierAccounts.mockResolvedValue(operationalWorkspace());
    await withOperationalPage(async container => {
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        await act(async () => container.querySelector('[aria-label="تعديل مورد تجريبي"]').click());
        await act(async () => [...container.querySelectorAll('label')].find(label => label.textContent === 'ملابس').querySelector('input').click());
        await act(async () => container.querySelector('[data-testid="mezan-supplier-editor"]').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
        expect(loadMezanSuppliersWorkspace).toHaveBeenCalledTimes(2);
        expect(loadOperationalSupplierAccounts).toHaveBeenCalledTimes(1);
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
    });
});

test.each(['draft', 'frozen', 'accounting_active'])('native accounts wait for a verified inactive operational source: %s', async status => {
    let resolve;
    loadOperationalSupplierAccounts.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await withOperationalPage(async container => {
        loadMezanSupplierFinancials.mockResolvedValue({ financial_status: 'available', suppliers: [], invoices: [], timeline: [], summary: { outstanding_halalas: 8800 } });
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
        expect(container.textContent).not.toContain('إجمالي ديون الموردين');
        await act(async () => resolve({ active: false, status, authority: 'operational_balance_report' }));
        expect(loadMezanSupplierFinancials).toHaveBeenCalledTimes(1);
        const transport = loadOperationalSupplierAccounts.mock.calls[0][0];
        expect(transport).toEqual(expect.objectContaining({ signal: expect.any(AbortSignal), timeout: 20000 }));
        expect(loadMezanSupplierFinancials).toHaveBeenCalledWith({}, transport);
        expect(container.textContent).toContain('88.00');
    });
});

test.each([
    null, {}, { active: false, status: 'active', authority: 'operational_balance_report' },
    { active: false, status: 'draft', authority: 'unknown' },
    { active: true, status: 'active', authority: 'operational_balance_report' },
])('an invalid source response never certifies native accounts: %j', async response => {
    loadOperationalSupplierAccounts.mockResolvedValue(response);
    await withOperationalPage(async container => {
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
        expect(container.querySelector('[role="alert"]')).not.toBeNull();
        expect(container.textContent).not.toContain('إجمالي ديون الموردين');
    });
});

test('a direct supplier link is explicit account intent and opens the selected account', async () => {
    mockSupplierQuery = 'supplier=supplier-v2';
    loadOperationalSupplierAccounts.mockResolvedValue(operationalWorkspace());
    await withOperationalPage(async (container, root) => {
        expect(loadOperationalSupplierAccounts).toHaveBeenCalledTimes(1);
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
        expect(container.querySelector('[data-testid="operational-supplier-detail"]')).not.toBeNull();
        mockSupplierQuery = '';
        await act(async () => root.render(<MezanSuppliersV2 />));
        expect(container.querySelector('[data-testid="operational-supplier-detail"]')).toBeNull();
        expect(container.querySelector('[data-testid="mezan-supplier-accounts-load"]').textContent).toBe('تحديث حسابات الموردين');
        expect(loadOperationalSupplierAccounts).toHaveBeenCalledTimes(1);
    });
});

test('a hidden direct link starts neither administrative nor financial reads until visible', async () => {
    mockSupplierQuery = 'supplier=supplier-v2';
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
    loadOperationalSupplierAccounts.mockResolvedValue(operationalWorkspace());
    await withOperationalPage(async container => {
        expect(loadMezanSuppliersWorkspace).not.toHaveBeenCalled();
        expect(loadOperationalSupplierAccounts).not.toHaveBeenCalled();
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
        await act(async () => {
            Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
            document.dispatchEvent(new Event('visibilitychange'));
        });
        expect(loadMezanSuppliersWorkspace).toHaveBeenCalledTimes(1);
        expect(loadOperationalSupplierAccounts).toHaveBeenCalledTimes(1);
        expect(container.querySelector('[data-testid="operational-supplier-detail"]')).not.toBeNull();
    });
});

test('hiding aborts a requested source read and its late inactive result cannot start native loading', async () => {
    let resolve;
    loadOperationalSupplierAccounts.mockImplementationOnce(() => new Promise(done => { resolve = done; }))
        .mockResolvedValue(operationalWorkspace());
    await withOperationalPage(async container => {
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        const transport = loadOperationalSupplierAccounts.mock.calls[0][0];
        await act(async () => {
            Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
            document.dispatchEvent(new Event('visibilitychange'));
        });
        expect(transport.signal.aborted).toBe(true);
        await act(async () => resolve({ active: false, status: 'draft', authority: 'operational_balance_report' }));
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
        expect(container.querySelector('[data-testid="operational-supplier-summary"]')).toBeNull();
        await act(async () => {
            Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
            document.dispatchEvent(new Event('visibilitychange'));
        });
        expect(loadOperationalSupplierAccounts).toHaveBeenCalledTimes(2);
        expect(container.textContent).toContain('135.00');
    });
});

test('unmount aborts the selected native read without touching any payment writer', async () => {
    await withOperationalPage(async (container, root) => {
        loadMezanSupplierFinancials.mockImplementationOnce(() => new Promise(() => {}));
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        const transport = loadMezanSupplierFinancials.mock.calls[0][1];
        expect(transport.signal.aborted).toBe(false);
        await act(async () => root.render(null));
        expect(transport.signal.aborted).toBe(true);
    });
});

test('completed account reads do not poll on visibility or focus changes', async () => {
    loadOperationalSupplierAccounts.mockResolvedValue(operationalWorkspace());
    await withOperationalPage(async container => {
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        for (const state of ['hidden', 'visible']) {
            await act(async () => {
                Object.defineProperty(document, 'visibilityState', { configurable: true, value: state });
                document.dispatchEvent(new Event('visibilitychange'));
                window.dispatchEvent(new Event('focus'));
            });
        }
        expect(loadOperationalSupplierAccounts).toHaveBeenCalledTimes(1);
        expect(loadMezanSuppliersWorkspace).toHaveBeenCalledTimes(1);
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
    });
});

test('switching users cancels old account intent and discards late financial amounts', async () => {
    let resolve;
    loadOperationalSupplierAccounts.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await withOperationalPage(async (container, root) => {
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        const transport = loadOperationalSupplierAccounts.mock.calls[0][0];
        useOptionalAuth.mockReturnValue({ user: { id: 'other', role: 'owner' } });
        await act(async () => root.render(<MezanSuppliersV2 />));
        expect(transport.signal.aborted).toBe(true);
        await act(async () => resolve(operationalWorkspace()));
        expect(container.textContent).not.toContain('135.00');
        expect(container.querySelector('[data-testid="mezan-supplier-accounts-load"]').textContent).toBe('عرض حسابات الموردين');
        expect(loadOperationalSupplierAccounts).toHaveBeenCalledTimes(1);
        expect(loadMezanSupplierFinancials).not.toHaveBeenCalled();
    });
});

test('native accounts without a ready ledger never display default zero balances', async () => {
    await withOperationalPage(async container => {
        loadMezanSupplierFinancials.mockResolvedValue({ financial_status: 'not_ready',
            suppliers: [{ id: 'supplier-v2', financial: { outstanding_halalas: 0, advance_halalas: 0, real_invoice_count: 0 } }],
            summary: { outstanding_halalas: 0, paid_halalas: 0, advance_halalas: 0, real_invoice_count: 0 } });
        await act(async () => container.querySelector('[data-testid="mezan-supplier-accounts-load"]').click());
        expect(container.textContent).toContain('غير متاح');
        expect(container.textContent).not.toContain('0.00');
    });
});
