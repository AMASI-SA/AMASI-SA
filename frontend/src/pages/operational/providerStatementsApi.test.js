import http from "../../lib/api";
import { operationalApi as api } from "./api";

jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
beforeEach(() => { jest.resetAllMocks(); http.get.mockResolvedValue({ data: {} }); http.post.mockResolvedValue({ data: {} }); });

test("saved statement history and review use bounded cancellable GETs", async () => {
    const config = { signal: new AbortController().signal };
    await api.providerStatements(config);
    expect(http.get).toHaveBeenLastCalledWith("/operational-balances/provider-statements", { timeout: 30000, ...config });
    await api.providerStatementReview("statement/one", config);
    expect(http.get).toHaveBeenLastCalledWith("/operational-balances/provider-statements/statement%2Fone/review", { timeout: 30000, ...config });
    expect(http.post).not.toHaveBeenCalled();
});

test("explicit upload sends the file and stable scoped identity without a provider hint", async () => {
    const file = new File(["synthetic"], "statement.xlsx");
    const config = { signal: new AbortController().signal };
    await api.uploadProviderStatement(file, { request_id: "same-upload", expected_session_scope: "owner-period", expected_started_at: "2026-11-01T00:00:00Z" }, config);
    const [path, form, options] = http.post.mock.calls[0];
    expect(path).toBe("/operational-balances/provider-statements/upload");
    expect([...form.keys()]).toEqual(["file", "request_id", "expected_session_scope", "expected_started_at"]);
    expect(form.get("file")).toBe(file);
    expect(form.get("request_id")).toBe("same-upload");
    expect(form.get("expected_session_scope")).toBe("owner-period");
    expect(form.get("expected_started_at")).toBe("2026-11-01T00:00:00Z");
    expect(options).toEqual({ timeout: 60000, ...config });
    expect(http.get).not.toHaveBeenCalled();
});

test("confirmation forwards only the exact reviewed decision body and never refreshes balances", async () => {
    const body = { request_id: "same-confirm", expected_session_scope: "scope", expected_preview_hash: "preview",
        decisions: [{ row_id: "row-1", action: "accept_statement", existing_id: "record-1" }], confirmed: true };
    const config = { signal: new AbortController().signal };
    await api.confirmProviderStatement("statement/one", body, config);
    expect(http.post).toHaveBeenCalledTimes(1);
    expect(http.post).toHaveBeenCalledWith("/operational-balances/provider-statements/statement%2Fone/confirm", body, { timeout: 30000, ...config });
    expect(http.get).not.toHaveBeenCalled();
});
