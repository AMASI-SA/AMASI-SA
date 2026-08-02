import { renderToStaticMarkup } from "react-dom/server";

import TikTokReportingSyncControl from "./TikTokReportingSyncControl";

jest.mock("../../services/tiktokIntegrationsV2", () => ({
    syncTikTokReporting: jest.fn(),
}));

test("TikTok reporting control is enabled only when the backend action is enabled", () => {
    const enabled = renderToStaticMarkup(
        <TikTokReportingSyncControl
            integration={{
                provider: "tiktok_ads",
                actions: { sync_data: { enabled: true, reason: null } },
            }}
        />,
    );
    expect(enabled).toContain('data-testid="integration-tiktok_ads-sync"');
    expect(enabled).not.toContain("disabled");
    expect(enabled).toContain("مزامنة 30 يوم");
    expect(enabled).toContain("دون تعديل الحملات أو المحاسبة أو قيود");

    const disabled = renderToStaticMarkup(
        <TikTokReportingSyncControl
            integration={{
                provider: "tiktok_ads",
                actions: {
                    sync_data: {
                        enabled: false,
                        reason: "اربط TikTok Marketing API أولًا.",
                    },
                },
            }}
        />,
    );
    expect(disabled).toContain("disabled");
    expect(disabled).toContain("اربط TikTok Marketing API أولًا.");
});
