import { act } from "react";
import { createRoot } from "react-dom/client";
import SpecificationChangeNotice from "./SpecificationChangeNotice";

let container;
let root;

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("unchanged options do not show a change bell", () => {
    act(() => root.render(<SpecificationChangeNotice specification={{ name: "المقاس", value: "M" }} />));
    expect(container.querySelector("button")).toBeNull();
});

test("the bell opens the old and new field names and values and closes without changing data", () => {
    const specification = Object.freeze({
        name: "نوع المنتج",
        value: "سويتر + ترنج",
        change: Object.freeze({
            previous_name: "المنتج",
            previous_value: "بلوفر + ترنج",
            current_name: "نوع المنتج",
            current_value: "سويتر + ترنج",
        }),
    });
    act(() => root.render(<SpecificationChangeNotice specification={specification} />));
    const bell = container.querySelector('button[aria-label="عرض تغييرات نوع المنتج"]');
    expect(bell).not.toBeNull();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    act(() => bell.click());
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog.getAttribute("dir")).toBe("rtl");
    expect(dialog.querySelector('[aria-label="القديم"]').textContent).toContain("المنتج");
    expect(dialog.querySelector('[aria-label="القديم"]').textContent).toContain("بلوفر + ترنج");
    expect(dialog.querySelector('[aria-label="الجديد"]').textContent).toContain("نوع المنتج");
    expect(dialog.querySelector('[aria-label="الجديد"]').textContent).toContain("سويتر + ترنج");
    act(() => dialog.querySelector('button[aria-label="إغلاق تفاصيل التغيير"]').click());
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(specification.value).toBe("سويتر + ترنج");
});
