import { openCurrentCarrierLabel } from "./openCurrentCarrierLabel";
import { refreshCompletedOrderCarrierLabel } from "../services/fulfillmentV2";
import { printStoreCourierLabel } from "./storeCourierLabelPrint";

jest.mock("../services/fulfillmentV2", () => ({
    refreshCompletedOrderCarrierLabel: jest.fn(),
}));
jest.mock("./storeCourierLabelPrint", () => ({
    printStoreCourierLabel: jest.fn(),
}));

describe("current Salla carrier label window", () => {
    let printable;
    let openSpy;

    beforeEach(() => {
        printable = {
            opener: {},
            document: { title: "" },
            location: { replace: jest.fn() },
            close: jest.fn(),
        };
        openSpy = jest.spyOn(window, "open").mockReturnValue(printable);
        jest.clearAllMocks();
    });

    afterEach(() => {
        openSpy.mockRestore();
    });

    test("reserves the popup before network read and opens only verified AWB", async () => {
        let resolve;
        refreshCompletedOrderCarrierLabel.mockReturnValue(
            new Promise((done) => { resolve = done; }),
        );
        const promise = openCurrentCarrierLabel("298000001");
        expect(window.open).toHaveBeenCalledWith("about:blank", "_blank");
        expect(printable.document.title).toContain("جلب");
        expect(printable.location.replace).not.toHaveBeenCalled();
        resolve({
            order_status_completed: true,
            ready: true,
            label_url: "https://files.example/label.pdf",
        });
        await promise;
        expect(printable.location.replace).toHaveBeenCalledWith(
            "https://files.example/label.pdf",
        );
        expect(printable.close).not.toHaveBeenCalled();
    });

    test("does not open a nonexistent shipment PDF", async () => {
        refreshCompletedOrderCarrierLabel.mockResolvedValue({
            order_status_completed: true,
            ready: false,
            message: "لا توجد بوليصة",
        });
        await expect(openCurrentCarrierLabel("298000001")).rejects.toThrow("لا توجد بوليصة");
        expect(printable.location.replace).not.toHaveBeenCalled();
        expect(printable.close).toHaveBeenCalledTimes(1);
    });

    test("closes reserved popup when the order changes while fetching", async () => {
        let isCurrent = true;
        refreshCompletedOrderCarrierLabel.mockImplementation(async () => {
            isCurrent = false;
            return {
                order_status_completed: true,
                ready: true,
                label_url: "https://files.example/label.pdf",
            };
        });
        await openCurrentCarrierLabel("298000001", () => isCurrent);
        expect(printable.location.replace).not.toHaveBeenCalled();
        expect(printable.close).toHaveBeenCalledTimes(1);
    });

    test("prints merchant courier without substituting a carrier PDF", async () => {
        refreshCompletedOrderCarrierLabel.mockResolvedValue({
            order_status_completed: true,
            ready: true,
            label_type: "store_courier",
            print_data: { qr_code: "data:image/svg+xml;base64,abc" },
        });
        printStoreCourierLabel.mockReturnValue(true);
        await openCurrentCarrierLabel("298000001");
        expect(printStoreCourierLabel).toHaveBeenCalledWith(
            printable, { qr_code: "data:image/svg+xml;base64,abc" },
        );
        expect(printable.location.replace).not.toHaveBeenCalled();
    });
});
