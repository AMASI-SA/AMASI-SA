import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import ProductPriceChangeRequest from "./ProductPriceChangeRequest";

let mockRequestId = "request-1";
let mockUser = { id: "owner-1", is_owner: true };
jest.mock("react-router-dom", () => ({
    useParams: () => ({ requestId: mockRequestId }),
    Link: ({ to, children }) => <a href={to}>{children}</a>,
}));
jest.mock("../context/AuthContext", () => ({
    useAuth: () => ({ user: mockUser, loading: false, authStatus: "authenticated" }),
}));
jest.mock("../lib/api", () => ({
    __esModule: true,
    default: { get: jest.fn(), post: jest.fn() },
    formatApiErrorDetail: detail => detail == null ? "حدث خطأ ما، يرجى المحاولة لاحقاً." : typeof detail === "string" ? detail : String(detail),
}));

const pending = {
    id: "request-1", status: "pending", source: "supplier_invoice", change_type: "product_price",
    product_id: "product-1", product_name: "شال شتوي", variant_id: null,
    before_halalas: 1500, after_halalas: 1800, requested_by: "employee-1", requested_by_name: "خالد",
    invoice_id: "invoice-1", created_at: "2026-11-08T08:00:00+00:00", decided_at: null,
    invoice_before_halalas: 2500, invoice_after_halalas: 2800, reference_option_cost_halalas: 1000,
};
const response = request => ({ data: { ok: true, request } });
const approved = { ...pending, status: "approved", decided_at: "2026-11-08T09:00:00+00:00" };
const rejected = { ...pending, status: "rejected", decided_at: "2026-11-08T09:00:00+00:00" };
const deferred = () => {
    let resolve;
    let reject;
    const promise = new Promise((a, b) => { resolve = a; reject = b; });
    return { promise, resolve, reject };
};
let node;
let root;
const query = id => node.querySelector(`[data-testid="${id}"]`);
const approve = () => query("price-change-approve");
const reject = () => query("price-change-reject");
const refresh = () => query("price-change-refresh");
const render = () => act(async () => root.render(<ProductPriceChangeRequest />));
const click = button => act(async () => button.click());

beforeEach(() => {
    jest.resetAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    mockRequestId = "request-1";
    mockUser = { id: "owner-1", is_owner: true };
    node = document.createElement("div");
    document.body.appendChild(node);
    root = createRoot(node);
    api.get.mockResolvedValue(response(pending));
    api.post.mockResolvedValue(response(approved));
});
afterEach(() => {
    act(() => root.unmount());
    node.remove();
    jest.restoreAllMocks();
});

test("opening shows the exact catalog proposal, employee and invoice without saving anything", async () => {
    await render();
    expect(api.get).toHaveBeenCalledWith("/price-change-requests-v1/request-1", expect.objectContaining({ timeout: 15000 }));
    expect(node.textContent).toContain("شال شتوي");
    expect(node.textContent).toContain("خالد");
    expect(node.textContent).toContain("invoice-1");
    expect(query("price-change-before").textContent).toBe("15.00 ر.س.");
    expect(query("price-change-after").textContent).toBe("18.00 ر.س.");
    expect(node.textContent).not.toContain("28.00");
    expect(node.textContent).toContain("سعر فاتورة المورد يبقى كما سجّله الموظف");
    expect(approve().disabled).toBe(false);
    expect(api.post).not.toHaveBeenCalled();
});

test("rapid approval clicks submit one immutable decision and wait for its response", async () => {
    const write = deferred();
    api.post.mockReturnValue(write.promise);
    await render();
    act(() => { approve().click(); approve().click(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith("/price-change-requests-v1/request-1/decision", { decision: "approve" },
        { timeout: 20000, _mezanAuthRetried: true });
    expect(approve().disabled).toBe(true);
    expect(reject().disabled).toBe(true);
    expect(node.textContent).not.toContain("تم اعتماد السعر الجديد");
    await act(async () => write.resolve(response(approved)));
    expect(node.textContent).toContain("تم اعتماد السعر الجديد");
    expect(approve()).toBeNull();
    expect(reject()).toBeNull();
});

test("rejecting the proposal only posts reject to its own request", async () => {
    api.post.mockResolvedValue(response(rejected));
    await render();
    await click(reject());
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0].slice(0, 2)).toEqual(["/price-change-requests-v1/request-1/decision", { decision: "reject" }]);
    expect(node.textContent).toContain("تم رفض تغيير السعر");
});

test("catalog drift prevents another approval while permitting explicit rejection", async () => {
    api.post.mockRejectedValueOnce({ response: { status: 409, data: { detail: { code: "price_change_stale", message: "السعر تغيّر" } } } });
    await render();
    await click(approve());
    expect(node.textContent).toContain("تغيّر السعر المعتمد بعد إنشاء هذا الطلب");
    expect(approve().disabled).toBe(true);
    expect(reject().disabled).toBe(false);
    await click(refresh());
    expect(approve().disabled).toBe(true);
    api.post.mockResolvedValue(response(rejected));
    await click(reject());
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][1]).toEqual({ decision: "reject" });
});

test("an uncertain approval is recovered by GET without replaying the write", async () => {
    api.get.mockResolvedValueOnce(response(pending)).mockResolvedValueOnce(response(approved));
    api.post.mockRejectedValue({ code: "ECONNABORTED" });
    const updates = jest.fn();
    window.addEventListener("mezan:price-change-decided", updates);
    try {
        await render();
        await click(approve());
        expect(api.post).toHaveBeenCalledTimes(1);
        expect(api.get).toHaveBeenCalledTimes(2);
        expect(node.textContent).toContain("تم اعتماد السعر الجديد");
        expect(updates).toHaveBeenCalledTimes(1);
        expect(node.textContent).not.toContain("تعذر التأكد");
    } finally {
        window.removeEventListener("mezan:price-change-decided", updates);
    }
});

test("failed readback blocks decisions until a fresh read and only the same explicit retry is allowed", async () => {
    api.get.mockResolvedValueOnce(response(pending)).mockRejectedValueOnce(new Error("offline"));
    api.post.mockRejectedValueOnce({ code: "ECONNABORTED" }).mockResolvedValueOnce(response(approved));
    await render();
    await click(approve());
    expect(approve().disabled).toBe(true);
    expect(reject().disabled).toBe(true);
    expect(api.post).toHaveBeenCalledTimes(1);
    api.get.mockResolvedValue(response(pending));
    await click(refresh());
    expect(approve().textContent).toBe("إعادة إرسال الموافقة");
    expect(approve().disabled).toBe(false);
    expect(reject().disabled).toBe(true);
    expect(api.post).toHaveBeenCalledTimes(1);
    await click(approve());
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls.map(call => call[1])).toEqual([{ decision: "approve" }, { decision: "approve" }]);
});

test("manual recovery displays the confirmed result and clears uncertainty", async () => {
    api.get.mockResolvedValueOnce(response(pending)).mockRejectedValueOnce(new Error("offline"));
    api.post.mockRejectedValue({ code: "ECONNABORTED" });
    await render();
    await click(approve());
    api.get.mockResolvedValue(response(approved));
    await click(refresh());
    expect(node.textContent).toContain("تم اعتماد السعر الجديد");
    expect(node.textContent).not.toContain("تعذر التأكد");
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("an already-decided conflict reads the actual rejection and never reports approval", async () => {
    api.get.mockResolvedValueOnce(response(pending)).mockResolvedValueOnce(response(rejected));
    api.post.mockRejectedValue({ response: { status: 409, data: { detail: { code: "price_change_already_decided" } } } });
    await render();
    await click(approve());
    expect(node.textContent).toContain("تم رفض تغيير السعر");
    expect(node.textContent).not.toContain("تم اعتماد السعر الجديد");
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("a delayed previous request cannot replace the newly opened request", async () => {
    const old = deferred();
    api.get.mockReturnValueOnce(old.promise);
    await render();
    mockRequestId = "request-2";
    api.get.mockResolvedValue(response({ ...pending, id: "request-2", product_name: "فروة أطفال" }));
    await render();
    await act(async () => old.resolve(response(pending)));
    expect(node.textContent).toContain("فروة أطفال");
    expect(node.textContent).not.toContain("شال شتوي");
    api.post.mockResolvedValue(response({ ...approved, id: "request-2", product_name: "فروة أطفال" }));
    await click(approve());
    expect(api.post.mock.calls[0][0]).toBe("/price-change-requests-v1/request-2/decision");
});

test("a previous account's pending reply cannot act on the next account's page", async () => {
    const write = deferred();
    api.post.mockReturnValue(write.promise);
    await render();
    act(() => approve().click());
    mockUser = { id: "employee-1", is_owner: false };
    await render();
    await act(async () => write.resolve(response(approved)));
    expect(node.textContent).toContain("متاحة لمالك المتجر فقط");
    expect(node.textContent).not.toContain("تم اعتماد السعر الجديد");
    expect(approve()).toBeNull();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledTimes(1);
});

test("an employee cannot load or decide owner price requests", async () => {
    mockUser = { id: "employee-1", role: "employee", is_owner: false };
    await render();
    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
    expect(approve()).toBeNull();
});

test("failed initial loading offers only a read retry", async () => {
    api.get.mockRejectedValueOnce(new Error("offline"));
    await render();
    expect(node.textContent).toContain("تعذر تحميل حالة الطلب");
    expect(approve()).toBeNull();
    api.get.mockResolvedValue(response(pending));
    await click(refresh());
    expect(approve().disabled).toBe(false);
    expect(api.post).not.toHaveBeenCalled();
});

test.each([
    { id: "another-request" }, { before_halalas: "1500" }, { after_halalas: 18.5 }, { status: "unknown" },
    { change_type: "unknown" }, { after_halalas: null, removes_override: false },
    { before_quantity: "1" }, { after_quantity: -2 },
])("unverified response %j never offers approval", async patch => {
    api.get.mockResolvedValue(response({ ...pending, ...patch }));
    await render();
    expect(node.textContent).toContain("تعذر التحقق من تفاصيل طلب تغيير السعر");
    expect(approve()).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
});

test.each([approved, rejected])("an existing $status request has no repeat action", async request => {
    api.get.mockResolvedValue(response(request));
    await render();
    expect(approve()).toBeNull();
    expect(reject()).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
});

test("the request distinguishes a service from its product and a direct customer-option price", async () => {
    api.get.mockResolvedValue(response({ ...pending, change_type: "service_price", service_id: "service-1", service_name: "تطريز الاسم" }));
    await render();
    expect(node.querySelector("h1").textContent).toContain("سعر الخدمة");
    expect(node.querySelector("h2").textContent).toBe("تطريز الاسم");
    expect(node.textContent).toContain("المنتج: شال شتوي");
    api.get.mockResolvedValue(response({ ...pending, option_name: "إضافة الاسم", value_name: "نعم" }));
    await click(refresh());
    expect(node.textContent).toContain("الخيار: إضافة الاسم — نعم");
});

test("an unset previous price is displayed as unset rather than zero", async () => {
    api.get.mockResolvedValue(response({ ...pending, before_halalas: null }));
    await render();
    expect(query("price-change-before").textContent).toBe("غير محدد");
    expect(approve().disabled).toBe(false);
    expect(api.post).not.toHaveBeenCalled();
});

test("a direct option quantity change shows the old and proposed quantities even when unit price is unchanged", async () => {
    api.get.mockResolvedValue(response({ ...pending, option_id: "gift", option_name: "تغليف", value_name: "نعم",
        before_halalas: 1000, after_halalas: 1000, before_quantity: 1, after_quantity: 2 }));
    await render();
    expect(query("price-change-before").textContent).toBe("10.00 ر.س.");
    expect(query("price-change-after").textContent).toBe("10.00 ر.س.");
    expect(query("price-change-before-quantity").textContent).toContain("1");
    expect(query("price-change-after-quantity").textContent).toContain("2");
    expect(node.textContent).toContain("لكل وحدة، بكمية 2 لهذا الخيار");
    expect(approve().disabled).toBe(false);
    expect(api.post).not.toHaveBeenCalled();
});

test("removing a saved price is explicit and never shown as a zero-price proposal", async () => {
    const removal = { ...pending, after_halalas: null, removes_override: true };
    api.get.mockResolvedValue(response(removal));
    api.post.mockResolvedValue(response({ ...removal, status: "approved" }));
    await render();
    expect(query("price-change-after").textContent).toBe("إلغاء السعر المسجل");
    expect(node.textContent).not.toContain("0.00");
    expect(node.textContent).toContain("هل توافق على إلغاء السعر المسجل");
    await click(approve());
    expect(node.textContent).toContain("تم اعتماد إلغاء السعر المسجل");
});

test("owner-only server errors show their Arabic message and block writes until reloaded", async () => {
    api.post.mockRejectedValue({ response: { status: 403, data: { detail: {
        code: "price_change_owner_required", message: "اعتماد السعر متاح لمالك المتجر من لوحة التحكم فقط.",
    } } } });
    await render();
    await click(approve());
    expect(node.textContent).toContain("اعتماد السعر متاح لمالك المتجر");
    expect(node.textContent).not.toContain("[object Object]");
    expect(approve().disabled).toBe(true);
    expect(reject().disabled).toBe(true);
    await click(refresh());
    expect(approve().disabled).toBe(false);
    expect(api.post).toHaveBeenCalledTimes(1);
});
