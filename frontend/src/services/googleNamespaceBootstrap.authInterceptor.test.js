import axios from "axios";
import api from "../lib/api";
import fixture from "./__fixtures__/googleNamespaceBootstrap.json";
import { readGoogleBootstrapReceipt, prepareGoogleNamespaceBootstrap, executeGoogleNamespaceBootstrap } from "./googleNamespaceBootstrap";

let previousAdapter;
beforeEach(() => { previousAdapter = api.defaults.adapter; jest.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-30T12:00:00Z")); });
afterEach(() => { api.defaults.adapter = previousAdapter; jest.restoreAllMocks(); });
test.each(["receipt", "prepare", "execute"])("real Axios interceptor cannot refresh or replay %s on 401", async (operation) => {
    const refresh = jest.spyOn(axios, "post").mockRejectedValue(new Error("refresh must not be reached"));
    const calls = [];
    api.defaults.adapter = async (config) => {
        calls.push(config);
        throw Object.assign(new Error("synthetic session denied"), { config, response: { status: 401, data: {}, config } });
    };
    const action = operation === "receipt" ? () => readGoogleBootstrapReceipt() : operation === "prepare"
        ? () => prepareGoogleNamespaceBootstrap("a".repeat(32)) : () => executeGoogleNamespaceBootstrap(fixture.prepared);
    await expect(action()).rejects.toMatchObject({ response: { status: 401 } });
    expect(calls).toHaveLength(1); expect(calls[0]._mezanAuthRetried).toBe(true);
    expect(calls[0].url.endsWith("/" + operation)).toBe(true); expect(refresh).not.toHaveBeenCalled();
});
