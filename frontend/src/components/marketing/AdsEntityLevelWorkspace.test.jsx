import React, { act } from "react";
import { createRoot } from "react-dom/client";

jest.mock("./CampaignManagerTable", () => function CampaignTable() {
    return <div data-testid="mock-campaign-table">الحملات</div>;
});

jest.mock("./AdSquadManagerTable", () => function AdSquadTable() {
    return <div data-testid="mock-ad-squad-table">المجموعات الإعلانية</div>;
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

    test("enables Snapchat Ad Squads and keeps Ads disabled", async () => {
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
        expect(ads.disabled).toBe(true);

        await act(async () => groups.click());
        expect(onChange).toHaveBeenCalledWith("ad_squads");
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

        expect(container.querySelector('[data-testid="mock-ad-squad-table"]')).not.toBeNull();
    });
});
