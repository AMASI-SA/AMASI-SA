import XLSX from "./safeSpreadsheet";

let createURL, revokeURL, click, originalCreate, originalRevoke;
beforeEach(() => {
    originalCreate = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    originalRevoke = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    createURL = jest.fn(() => "blob:scoped-export"); revokeURL = jest.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeURL });
    click = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => {
    jest.restoreAllMocks();
    if (originalCreate) Object.defineProperty(URL, "createObjectURL", originalCreate); else delete URL.createObjectURL;
    if (originalRevoke) Object.defineProperty(URL, "revokeObjectURL", originalRevoke); else delete URL.revokeObjectURL;
});

test("default spreadsheet callers still download once and release the object URL", async () => {
    const workbook = { xlsx: { writeBuffer: jest.fn().mockResolvedValue(new Uint8Array([1, 2, 3])) } };
    await expect(XLSX.writeFile(workbook, "report.xlsx")).resolves.toBeUndefined();
    expect(workbook.xlsx.writeBuffer).toHaveBeenCalledTimes(1);
    expect(createURL).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect(click.mock.instances[0].download).toBe("report.xlsx");
    expect(revokeURL).toHaveBeenCalledWith("blob:scoped-export");
    expect(document.querySelector('a[download="report.xlsx"]')).toBeNull();
});

test("revoked scope during real writeFile serialization creates no URL or download anchor", async () => {
    let resolve; const buffer = new Promise(done => { resolve = done; });
    const workbook = { xlsx: { writeBuffer: jest.fn().mockReturnValue(buffer) } };
    let allowed = true; const shouldContinue = jest.fn(() => allowed);
    const result = XLSX.writeFile(workbook, "private.xlsx", { shouldContinue });
    expect(workbook.xlsx.writeBuffer).toHaveBeenCalledTimes(1);
    allowed = false; resolve(new Uint8Array([1, 2, 3]));
    await expect(result).resolves.toBe(false);
    expect(shouldContinue).toHaveBeenCalledTimes(2);
    expect(createURL).not.toHaveBeenCalled();
    expect(click).not.toHaveBeenCalled();
    expect(document.querySelector('a[download="private.xlsx"]')).toBeNull();
});

test("already revoked scope skips serialization itself", async () => {
    const workbook = { xlsx: { writeBuffer: jest.fn() } };
    await expect(XLSX.writeFile(workbook, "private.xlsx", { shouldContinue: () => false })).resolves.toBe(false);
    expect(workbook.xlsx.writeBuffer).not.toHaveBeenCalled();
    expect(createURL).not.toHaveBeenCalled(); expect(click).not.toHaveBeenCalled();
});
