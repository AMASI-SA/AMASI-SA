import api from "./api";
import { openShippingLabel, requiresPrivateLabel } from "./privateShippingLabel";
jest.mock("./api", () => ({ __esModule: true, default: { get: jest.fn() } }));
const path = "/api/special-orders-v1/12b8536b-a65e-4f71-9848-431dc5b0d908/label";
let popup;
beforeEach(() => {
    jest.useFakeTimers();
    api.get.mockReset();
    URL.createObjectURL = jest.fn(() => "blob:synthetic-private-label");
    URL.revokeObjectURL = jest.fn();
    popup = { opener: {}, closed: false, location: { replace: jest.fn() }, close: jest.fn() };
});
afterEach(() => { jest.clearAllTimers(); jest.useRealTimers(); });
test("private label is authenticated and kept until viewer closes", async () => {
    api.get.mockResolvedValue({ data: new Blob(["%PDF-synthetic"], { type: "application/pdf" }) });
    await openShippingLabel({ ready: true, label_url: path, label_requires_authorization: true }, popup);
    expect(api.get).toHaveBeenCalledWith(path.slice(4), { responseType: "blob" });
    expect(popup.location.replace).toHaveBeenCalledWith("blob:synthetic-private-label");
    jest.advanceTimersByTime(30000);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    popup.closed = true; jest.advanceTimersByTime(1000);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(popup.opener).toBeNull();
});
test.each(["https://attacker.invalid/label", `${path}?token=forged`, `${path}/../other`, "javascript:alert(1)"])("private authorization never follows arbitrary %s", async (url) => {
    await expect(openShippingLabel({ ready: true, label_url: url, label_requires_authorization: true }, popup)).rejects.toThrow();
    expect(api.get).not.toHaveBeenCalled(); expect(popup.close).toHaveBeenCalled();
});
test("legacy cached private path still uses authentication", () => {
    expect(requiresPrivateLabel({ label_url: path })).toBe(true);
});
test.each(["text/html", "application/json", "image/svg+xml"])("unsafe response %s is never rendered", async (mime) => {
    api.get.mockResolvedValue({ data: new Blob(["unsafe"], { type: mime }) });
    await expect(openShippingLabel({ ready: true, label_url: path }, popup)).rejects.toThrow();
    expect(popup.location.replace).not.toHaveBeenCalled();
});
test("ordinary carrier receives no API authorization", async () => {
    await openShippingLabel({ ready: true, label_url: "https://carrier.example.invalid/verified.pdf" }, popup);
    expect(api.get).not.toHaveBeenCalled();
    expect(popup.location.replace).toHaveBeenCalledWith("https://carrier.example.invalid/verified.pdf");
});
