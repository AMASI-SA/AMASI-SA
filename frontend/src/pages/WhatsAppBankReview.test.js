import fs from "fs";
import path from "path";

// Source contract check: the bank review screen must never become a payment
// confirmation button or accidentally re-use legacy GL-writing routes.
// Actual authenticated backend endpoints have independent RBAC tests.
const page = fs.readFileSync(
    path.join(__dirname, "WhatsAppBankReview.jsx"), "utf8",
);
const sidebar = fs.readFileSync(
    path.join(__dirname, "../components/Sidebar.jsx"), "utf8",
);
const app = fs.readFileSync(
    path.join(__dirname, "../App.js"), "utf8",
);
const v2 = fs.readFileSync(
    path.join(__dirname, "../components/MezanV2NavigationShell.jsx"), "utf8",
);
const layout = fs.readFileSync(
    path.join(__dirname, "../components/Layout.jsx"), "utf8",
);

describe("read-only WhatsApp bank evidence review", () => {
    test("only GETs owner-scoped review endpoints", () => {
        expect(page).toContain('api.get(BASE + "/worklist"');
        expect(page).toContain('api.get(BASE + "/" + encodeURIComponent(selected.intent_id))');
        expect(page).not.toMatch(/api\.(post|put|patch|delete)\s*\(/i);
        expect(page).not.toContain("/bank-transfer-review/");
        expect(page).not.toContain("/orders/status");
        expect(page).not.toContain("mark-paid");
    });

    test("requires bank_receipts.view and never displays untrusted receipt URLs", () => {
        expect(sidebar).toContain('requiredPermission: "bank_receipts.view"');
        expect(sidebar).toContain("granted.includes(it.requiredPermission)");
        expect(page).toContain('data?.read_only !== true');
        expect(page).toContain("selected.source_message_id");
        expect(page).not.toContain("receipt_image_path");
        expect(page).not.toContain("recipient_iban");
        expect(page).not.toContain("bank_reference");
    });

    test("uses separate protected route, not legacy confirmation page", () => {
        expect(app).toContain('import WhatsAppBankReview from "./pages/WhatsAppBankReview"');
        expect(app).toContain('path="/whatsapp-bank-review"');
        expect(app).toContain("<WhatsAppBankReview />");
        expect(page).toContain("لم يؤكد النظام وصول المبلغ");
        expect(page).toContain("لا توجد بها إجراءات لتأكيد الدفع");
        expect(v2).toContain("bankReviewAllowed");
        expect(layout).toContain("bankReviewAllowed={user?.is_owner");
        expect(v2).toContain('requiredPermission: "bank_receipts.view"');
    });
});
