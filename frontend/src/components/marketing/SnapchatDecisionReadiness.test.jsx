import React, { act } from "react";
import { readFileSync } from "fs";
import path from "path";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import SnapchatDecisionReadiness from "./SnapchatDecisionReadiness";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
const date = "2026-09-16";
const response = () => ({
    phase: "decision-intelligence-phase5-v1", mode: "recommendation_shadow",
    account: { id: "account-a" }, period: { date_from: date, date_to: date, closed: true },
    decision_ready: true,
    gates: Object.fromEntries(["contract", "period_closed", "coverage", "reconciliation", "freshness", "attribution", "financial_coverage"].map(key => [key, { passed: true }])),
});
describe("SnapchatDecisionReadiness", () => {
    let container, root;
    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        jest.clearAllMocks();
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });
    afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
    const render = async (accountId = "account-a", day = date) => act(async () => {
        root.render(<SnapchatDecisionReadiness accountId={accountId} date={day} />);
    });
    const click = async () => act(async () => container.querySelector("button").click());

    test("only reads the closed-day shadow on request and distinguishes passing data from activation", async () => {
        api.get.mockResolvedValue({ data: response() });
        await render();
        expect(api.get).not.toHaveBeenCalled();
        await click();
        expect(api.get).toHaveBeenCalledWith("/ads-manager/decision-intelligence/phase5/shadow", { params: {
            provider: "snapchat_ads", date_from: date, date_to: date, max_candidates: 1,
        } });
        expect(container.textContent).toContain("اجتازت الفترة شروط البيانات للتوصيات التجريبية");
        expect(container.textContent).toContain("لا تعني تفعيل الربط أو التنفيذ الآلي");
    });
    test("request matches the backend router mount rather than a mocked URL", async () => {
        const backend = path.resolve(process.cwd(), "../backend");
        const read = file => readFileSync(path.join(backend, file), "utf8");
        const server = read("server.py");
        const composition = read("ads_manager/__init__.py");
        const router = read("ads_manager/routes.py");
        const route = read("decision_intelligence/routes.py");
        expect(server).toContain("api.include_router(make_ads_manager_router(db, current_user))");
        expect(composition).toContain("attach_decision_intelligence_phase5_routes(");
        const prefix = router.match(/APIRouter\\(\\s*prefix="([^"]+)"/)[1];
        const endpoint = route.match(/@router\\.get\\("([^"]+)"/)[1];
        api.get.mockResolvedValue({ data: response() });
        await render(); await click();
        expect(api.get.mock.calls[0][0]).toBe(prefix + endpoint);
    });
    test("reports actual blockers and counts without declaring incomplete evidence ready", async () => {
        const data = response();
        data.decision_ready = false;
        data.gates.attribution = { passed: false, campaign_attribution: { unmatched_orders: 2 } };
        data.gates.financial_coverage = { passed: false, missing_cost_orders: 3 };
        api.get.mockResolvedValue({ data });
        await render(); await click();
        expect(container.textContent).toContain("اكتمال ربط الطلبات بالحملات: غير مكتمل");
        expect(container.textContent).toContain("طلبات ناقصة التكلفة خلال يوم الفحص: 3");
        expect(container.textContent).toContain("طلبات غير مرتبطة خلال يوم الفحص: 2");
        expect(container.textContent).not.toContain("اجتازت الفترة");
    });
    test("missing gates cannot pass even if aggregate readiness says true", async () => {
        const data = response(); delete data.gates.freshness;
        api.get.mockResolvedValue({ data });
        await render(); await click();
        expect(container.textContent).toContain("حداثة المزامنة: لم يُتحقق منه");
        expect(container.textContent).not.toContain("اجتازت الفترة");
    });
    test.each(["account", "period"])("rejects a response for a different %s", async field => {
        const data = response();
        if (field === "account") data.account.id = "other";
        else data.period.date_to = "2026-09-17";
        api.get.mockResolvedValue({ data });
        await render(); await click();
        expect(container.querySelector('[role="alert"]')).not.toBeNull();
        expect(container.textContent).not.toContain("اجتازت الفترة");
    });
    test("discards a late response after switching account", async () => {
        let finish;
        api.get.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
        await render(); await click(); await render("account-b");
        await act(async () => finish({ data: response() }));
        expect(container.textContent).not.toContain("اجتازت الفترة");
        expect(container.querySelector("button").disabled).toBe(false);
    });
    test("failed request remains unknown and permits retry", async () => {
        api.get.mockRejectedValue(new Error("unavailable"));
        await render(); await click();
        expect(container.querySelector('[role="alert"]')).not.toBeNull();
        api.get.mockResolvedValue({ data: response() });
        await click();
        expect(container.textContent).toContain("اجتازت الفترة");
    });
    test("cannot request before account and closed day are available", async () => {
        await render("", undefined);
        expect(container.querySelector("button").disabled).toBe(true);
        expect(api.get).not.toHaveBeenCalled();
    });
});
