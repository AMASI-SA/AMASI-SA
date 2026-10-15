import api from "../lib/api";
import { listWorkspaceProducts } from "./mezanProductsV2";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
    },
}));

beforeEach(() => {
    jest.clearAllMocks();
    api.get.mockResolvedValue({ data: { items: [] } });
});

test("sold missing-cost requests use the dedicated contract endpoint", async () => {
    await listWorkspaceProducts({
        page: 2,
        missingMezanCost: true,
        soldOnly: true,
        productIds: "p-1,p-2",
    });

    expect(api.get).toHaveBeenCalledWith(
        "/products-v2/workspace/sold-missing-cost-products",
        { params: expect.objectContaining({
            page: 2,
            missing_mezan_cost: true,
            sold_only: true,
            product_ids: "p-1,p-2",
        }) },
    );
});

test("ordinary product browsing keeps using the generic endpoint", async () => {
    await listWorkspaceProducts({ page: 2 });

    expect(api.get).toHaveBeenCalledWith(
        "/products-v2/workspace/products",
        { params: expect.objectContaining({
            page: 2,
            missing_mezan_cost: false,
            sold_only: false,
        }) },
    );
});
