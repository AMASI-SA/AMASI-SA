import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("./CampaignManagerTable", () => function CampaignTable({ onOpenAdSquads }) {
    return (
        <button
            type="button"
            data-testid="mock-campaign-table"
            onClick={() => onOpenAdSquads?.({
                campaign_id: "campaign-1",
                campaign_name: "حملة 1",
            })}
        >
            الحملات
        </button>
    );
});

jest.mock("./AdSquadManagerTable", () => function AdSquadTable({ onOpenAds }) {
    return (
        <button
            type="button"
            data-testid="mock-ad-squad-table"
            onClick={() => onOpenAds?.({
                ad_squad_id: "squad-1",
                ad_squad_name: "مجموعة 1",
                campaign_id: "campaign-1",
                campaign_name: "حملة 1",
            })}
        >
            المجموعات الإعلانية
        </button>
    );
});

jest.mock("./AdSquadSortControls", () => function AdSquadSortControls() {
    return <div data-testid="mock-ad-squad-sort">ترتيب المجموعات</div>;
});

jest.mock("./AdManagerTable", () => function AdManagerTable({ campaignId, adSquadId }) {
    return (
        <div
            data-testid="mock-ad-manager-table"
            data-campaign-id={campaignId || ""}
            data-ad-squad-id={adSquadId || ""}
        >
            الإعلانات
        </div>
    );
});

import AdsEntityLevelWorkspace from "./AdsEntityLevelWorkspace";

describe("AdsEntityLevelWorkspace", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
    });

    test("enables Snapchat Ad Squads and Ads", async () => {
        const onChange = jest.fn();
        await act(async () => {
            root.render(
                <AdsEntityLevelWorkspace
                    platform="snapchat"
                    platformLabel="سناب شات"
                    entityLevel="campaigns"
                    onEntityLevelChange={onChange}
                    campaigns={[]}
                    campaignTotals={{}}
                    campaignPagination={{}}
                    adSquadReport={null}
                />,
            );
        });

        expect(container.querySelector('[data-testid="mock-campaign-table"]')).not.toBeNull();
        const groups = container.querySelector('[data-testid="ads-entity-level-ad_squads"]');
        const ads = container.querySelector('[data-testid="ads-entity-level-ads"]');
        expect(groups.disabled).toBe(false);
        expect(ads.disabled).toBe(false);

        await act(async () => ads.click());
        expect(onChange).toHaveBeenCalledWith("ads");
    });

    test("renders the Ad Squad table for the selected entity level", async () => {
        await act(async () => {
            root.render(
                <AdsEntityLevelWorkspace
                    platform="snapchat"
                    platformLabel="سناب شات"
                    entityLevel="ad_squads"
                    onEntityLevelChange={() => {}}
                    campaigns={[]}
                    campaignTotals={{}}
                    campaignPagination={{}}
                    adSquadReport={{ ad_squads: [], totals: {}, pagination: {} }}
                />,
            );
        });

        expect(container.querySelector('[data-testid="mock-ad-squad-sort"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="mock-ad-squad-table"]')).not.toBeNull();
    });

    test("renders the Ads table for the selected entity level", async () => {
        await act(async () => {
            root.render(
                <AdsEntityLevelWorkspace
                    platform="snapchat"
                    platformLabel="سناب شات"
                    entityLevel="ads"
                    onEntityLevelChange={() => {}}
                    campaigns={[]}
                    campaignTotals={{}}
                    campaignPagination={{}}
                    adSquadReport={null}
                />,
            );
        });

        expect(container.querySelector('[data-testid="mock-ad-manager-table"]')).not.toBeNull();
    });

    test("passes hierarchy selections and exposes breadcrumb reset", async () => {
        const onOpenAdSquads = jest.fn();
        const onClearHierarchy = jest.fn();
        await act(async () => {
            root.render(
                <AdsEntityLevelWorkspace
                    platform="snapchat"
                    platformLabel="سناب شات"
                    entityLevel="ads"
                    onEntityLevelChange={() => {}}
                    campaigns={[]}
                    campaignTotals={{}}
                    campaignPagination={{}}
                    adSquadReport={null}
                    selectedCampaign={{
                        campaign_id: "campaign-1",
                        campaign_name: "حملة 1",
                    }}
                    selectedAdSquad={{
                        ad_squad_id: "squad-1",
                        ad_squad_name: "مجموعة 1",
                    }}
                    onOpenAdSquads={onOpenAdSquads}
                    onClearHierarchy={onClearHierarchy}
                />,
            );
        });

        const ads = container.querySelector('[data-testid="mock-ad-manager-table"]');
        expect(ads.dataset.campaignId).toBe("campaign-1");
        expect(ads.dataset.adSquadId).toBe("squad-1");
        const breadcrumb = container.querySelector('[data-testid="snapchat-entity-breadcrumb"]');
        expect(breadcrumb).not.toBeNull();

        const allCampaigns = Array.from(breadcrumb.querySelectorAll("button"))
            .find((button) => button.textContent.includes("كل الحملات"));
        await act(async () => allCampaigns.click());
        expect(onClearHierarchy).toHaveBeenCalledTimes(1);

        const campaign = Array.from(breadcrumb.querySelectorAll("button"))
            .find((button) => button.textContent.includes("حملة 1"));
        await act(async () => campaign.click());
        expect(onOpenAdSquads).toHaveBeenCalledWith({
            campaign_id: "campaign-1",
            campaign_name: "حملة 1",
        });
    });

});
