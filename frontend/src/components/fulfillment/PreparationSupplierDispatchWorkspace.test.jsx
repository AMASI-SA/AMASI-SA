import { renderToStaticMarkup } from "react-dom/server";

import PreparationSupplierDispatchWorkspace, {
    dispatchSelections,
} from "./PreparationSupplierDispatchWorkspace";


test("dispatch selection clamps quantities to the employee available pieces", () => {
    expect(dispatchSelections(
        [
            { group_key: "product:1", available_quantity: 3 },
            { group_key: "product:2", available_quantity: 1 },
        ],
        { "product:1": 9, "product:2": 0 },
    )).toEqual([
        { group_key: "product:1", quantity: 3 },
    ]);
});


test("new employee files are the default supplier handoff window", () => {
    const markup = renderToStaticMarkup(
        <PreparationSupplierDispatchWorkspace />,
    );

    expect(markup).toContain("جارٍ تحميل الملفات الجديدة");
});


test("supplier accounts and manager unassigned queue have independent windows", () => {
    const supplierMarkup = renderToStaticMarkup(
        <PreparationSupplierDispatchWorkspace view="supplier-accounts" />,
    );
    const managerMarkup = renderToStaticMarkup(
        <PreparationSupplierDispatchWorkspace view="unassigned" />,
    );

    expect(supplierMarkup).toContain("جارٍ تحميل حسابات الموردين");
    expect(managerMarkup).toContain("جارٍ تحميل المنتجات غير المسندة");
});
