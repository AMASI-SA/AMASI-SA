import api from "../lib/api";
import { allocateMezanSupplierPayment, loadMezanSupplierFinancials, loadMezanSuppliersWorkspace, loadOperationalSupplierAccounts, recordMezanSupplierPayment } from "./mezanSuppliersV2";

jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
beforeEach(() => { jest.clearAllMocks(); });

test("operational supplier workspace is a read of the report projection", async () => {
    api.get.mockResolvedValueOnce({ data: { active: true, authority: "operational_balance_report" } });
    expect(await loadOperationalSupplierAccounts()).toEqual({ active: true, authority: "operational_balance_report" });
    expect(api.get).toHaveBeenCalledWith("/operational-balances/suppliers", undefined);
    expect(api.post).not.toHaveBeenCalled();
});

test('supplier readers pass the visible request signal and bounded timeout without changing endpoints', async () => {
    const signal = new AbortController().signal;
    const config = { signal, timeout: 20000 };
    api.get.mockResolvedValue({ data: {} });
    await loadMezanSuppliersWorkspace(config);
    expect(api.get).toHaveBeenLastCalledWith('/suppliers-v2/workspace', config);
    await loadOperationalSupplierAccounts(config);
    expect(api.get).toHaveBeenLastCalledWith('/operational-balances/suppliers', config);
    await loadMezanSupplierFinancials({ supplierId: 'supplier-v2' }, config);
    expect(api.get).toHaveBeenLastCalledWith('/suppliers-v2/payment-workspace', { ...config, params: { supplier_id: 'supplier-v2' } });
    expect(api.post).not.toHaveBeenCalled();
});

test("financial workspace uses only the V2 payment endpoint and supplier filter", async () => {
    api.get.mockResolvedValueOnce({ data: { financial_status: "available" } });
    expect(await loadMezanSupplierFinancials({ supplierId: "supplier-v2" })).toEqual({ financial_status: "available" });
    expect(api.get).toHaveBeenCalledWith("/suppliers-v2/payment-workspace", { params: { supplier_id: "supplier-v2" } });
});

test("payment and allocation preserve supplied immutable operation identity", async () => {
    api.post.mockResolvedValue({ data: { id: "result" } });
    const payment = { operation_id: "uuid", amount: "100.00", financial_account_id: "canonical-bank" };
    await recordMezanSupplierPayment("supplier-v2", payment);
    expect(api.post).toHaveBeenLastCalledWith("/suppliers-v2/supplier-v2/payments", payment);
    const allocation = { operation_id: "allocation-uuid", amount: "25.00", payment_id: "payment-v2", invoice_id: "invoice-v2" };
    await allocateMezanSupplierPayment("supplier-v2", allocation);
    expect(api.post).toHaveBeenLastCalledWith("/suppliers-v2/supplier-v2/allocations", allocation);
});

test("payment rejection exposes status and Arabic business guidance", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 422, data: { detail: { code: "supplier_payment_future_date" } } } });
    await expect(recordMezanSupplierPayment("supplier-v2", {})).rejects.toMatchObject({ status: 422, code: "supplier_payment_future_date", message: "لا يمكن تسجيل السداد بتاريخ مستقبلي." });
});

test("unrecognized technical rejection is not exposed in product copy", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: "private_backend_code", message: "private internals" } } } });
    await expect(recordMezanSupplierPayment("supplier-v2", {})).rejects.toThrow("تعذّر تسجيل السداد");
});
