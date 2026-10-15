import { renderToStaticMarkup } from "react-dom/server";
import { InstagramSetupView } from "./InstagramCustomerIntelligenceIntegration";

jest.mock("react-router-dom", () => ({
    useNavigate: () => () => {},
}));

jest.mock("../services/customerIntelligence", () => ({
    connectInstagramCustomerIntelligence: jest.fn(),
    getInstagramCustomerIntelligenceSetup: jest.fn(),
}));

jest.mock("../services/metaIntegrationsV2", () => ({
    startMetaConnection: jest.fn(),
}));

describe("InstagramSetupView", () => {
    test("shows an opaque discovered account and receive-only confirmation", () => {
        const html = renderToStaticMarkup(
            <InstagramSetupView
                setup={{
                    state: "ready",
                    candidates: [{
                        candidate_ref: "instagram_candidate_opaque",
                        display_name: "متجر ميزان",
                    }],
                    receive_only: true,
                    send_allowed: false,
                    comment_reply_allowed: false,
                    ai_auto_reply_allowed: false,
                }}
                selectedRef="instagram_candidate_opaque"
            />,
        );

        expect(html).toContain("متجر ميزان");
        expect(html).toContain("تفعيل الاستقبال فقط");
        expect(html).toContain("لا أمنح ميزان صلاحية الإرسال أو الرد الآلي");
        expect(html).not.toContain("access_token");
    });

    test("connected state explicitly keeps sending and automation disabled", () => {
        const html = renderToStaticMarkup(
            <InstagramSetupView
                setup={{ state: "connected", candidates: [] }}
            />,
        );

        expect(html).toContain("Instagram مرتبط وجاهز للاستقبال");
        expect(html).toContain("يبقى الإرسال والرد على التعليقات والرد الآلي متوقفًا");
        expect(html).toContain("فتح ذكاء العملاء");
    });
});
