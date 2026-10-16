import React, { act } from "react";
import { createRoot } from "react-dom/client";
import CampaignAccountAnalysis from "./CampaignAccountAnalysis";

test("a new Meta-only cycle explains blocked Snapchat accounts without inventing advice", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const host = document.createElement("div");
    const root = createRoot(host);
    try {
        await act(async () => root.render(<CampaignAccountAnalysis snapshot={{ snapshot_id: "new", account_analysis: [
            { provider: "snapchat", account_id: "sa", account_name: "السعودي", status: "blocked", blocked_by: ["coverage", "financial_coverage"], recommendations_count: 0, period: {date_from: "2026-10-13", date_to: "2026-10-15", timezone: "Asia/Riyadh"} },
            { provider: "snapchat", account_id: "us", account_name: "الدولار", status: "no_recommendation", recommendations_count: 0 },
        ] }} />));
        expect(host.textContent).toContain("السعودي");
        expect(host.textContent).toContain("تغطية الحملات والمجموعات والإعلانات");
        expect(host.textContent).toContain("اكتمال التكاليف والربحية");
        expect(host.textContent).toContain("2026-10-13");
        expect(host.textContent).toContain("Asia/Riyadh");
        expect(host.textContent).toContain("دخلت بيانات الحساب، ولم تصدر له توصية");
        expect(host.querySelector("button")).toBeNull();
        await act(async () => root.render(<CampaignAccountAnalysis snapshot={{ snapshot_id: "old", recommendations: [] }} />));
        expect(host.textContent).toContain("لا يمكن تحديد سبب غياب");
        expect(host.textContent).not.toContain("السعودي");
    } finally {
        await act(async () => root.unmount());
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});
