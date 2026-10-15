import { resolveOrderCampaign } from "./orderCampaignAttribution";

describe("resolveOrderCampaign", () => {
    test("prefers the resolved campaign name over Salla's numeric UTM value", () => {
        expect(resolveOrderCampaign({
            source: {
                utm_campaign: "120248818886810420",
                campaign_id: "120248818886810420",
                campaign_name: "حملة ميتا للمبيعات",
            },
        })).toEqual({
            campaignId: "120248818886810420",
            campaignName: "حملة ميتا للمبيعات",
            rawCampaign: "120248818886810420",
            campaignDisplay: "حملة ميتا للمبيعات",
        });
    });

    test("does not present a copied campaign ID as a campaign name", () => {
        expect(resolveOrderCampaign({
            source: {
                utm_campaign: "120248818886810420",
                campaign_name: "120248818886810420",
            },
        })).toEqual({
            campaignId: "120248818886810420",
            campaignName: null,
            rawCampaign: "120248818886810420",
            campaignDisplay: "اسم الحملة غير متوفر",
        });
    });

    test("keeps a textual UTM campaign when no provider ID exists", () => {
        expect(resolveOrderCampaign({
            source: { utm_campaign: "حملة العودة للمدارس" },
        }).campaignDisplay).toBe("حملة العودة للمدارس");
    });
});
