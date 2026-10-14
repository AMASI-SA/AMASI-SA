import React, { act } from "react";
import { createRoot } from "react-dom/client";
import CampaignResearchStatus from "./CampaignResearchStatus";

let container, root;
beforeEach(() => {
    container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test("old recommendations remain unchanged and research status is explicit when supplied", async () => {
    await act(async () => root.render(<><p>التوصية الحالية</p><CampaignResearchStatus /></>));
    expect(container.textContent).toBe("التوصية الحالية");
    await act(async () => root.render(<><p>التوصية الحالية</p><CampaignResearchStatus research={{status:"not_connected",performed:false}} /></>));
    expect(container.textContent).toContain("التوصية الحالية");
    expect(container.querySelector('[data-testid="campaign-research-status"]').textContent).toContain("لم يُربط بعد");
    expect(container.querySelectorAll("img,video,iframe,button")).toHaveLength(0);
});

test("unknown or future research states do not invent a success result", async () => {
    await act(async () => root.render(<CampaignResearchStatus research={{status:"completed",performed:true}} />));
    expect(container.textContent).toBe("");
});

test("passive success explains the observed scope and untested areas", async () => {
    await act(async () => root.render(<CampaignResearchStatus research={{status:"partial",performed:true,mode:"passive_html_only"}} />));
    expect(container.textContent).toContain("فُحص نص صفحة منتج أماسي");
    expect(container.textContent).toContain("لم تُختبر");
    expect(container.querySelectorAll("img,video,iframe,button")).toHaveLength(0);
});
