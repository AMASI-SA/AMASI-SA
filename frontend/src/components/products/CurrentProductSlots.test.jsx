import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import CurrentProductSlots, { resizeCurrentSlots } from "./CurrentProductSlots";

let node;
let root;
const options = [
    { id: "first_size", name: "مقاس القطعة الأولى", values: [{ id: "m", name: "M" }, { id: "xl", name: "XL" }] },
    { id: "first_color", name: "لون القطعة الأولى", values: [{ id: "black", name: "أسود" }] },
    { id: "second_size", name: "مقاس القطعة الثانية", values: [{ id: "m", name: "M" }] },
    { id: "second_color", name: "لون القطعة الثانية", values: [{ id: "white", name: "أبيض" }] },
    { id: "note", name: "ملاحظة المورد", type: "text", values: [] },
];

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div");
    root = createRoot(node);
});
afterEach(async () => { await act(async () => root.unmount()); });

function Editor({ initial, onChange }) {
    const [row, setRow] = useState(initial);
    return <CurrentProductSlots row={row} variants={[]} options={options} onChange={(patch) => {
        setRow((previous) => ({ ...previous, ...patch }));
        onChange(patch);
    }} />;
}

const choiceBox = (piece, name) => [...node.querySelectorAll("label")].find((label) =>
    label.textContent === name && label.closest("fieldset")?.querySelector("legend")?.textContent === `اختيارات القطعة ${piece} لدى المورد`)?.querySelector("input");

test("assigns separate size and color fields to each piece without inventory variants", async () => {
    const changed = jest.fn();
    await act(async () => root.render(<Editor initial={{ quantity: "2", slots: [{ slot_id: "1" }, { slot_id: "2" }] }} onChange={changed} />));
    const firstSize = choiceBox(1, "مقاس القطعة الأولى");
    expect(firstSize).toBeDefined();
    await act(async () => firstSize.click());
    await act(async () => choiceBox(1, "لون القطعة الأولى").click());
    await act(async () => choiceBox(2, "مقاس القطعة الثانية").click());
    await act(async () => choiceBox(2, "لون القطعة الثانية").click());
    expect(changed.mock.calls.at(-1)[0].slots).toEqual([
        { slot_id: "1", selection_option_ids: ["first_size", "first_color"] },
        { slot_id: "2", selection_option_ids: ["second_size", "second_color"] },
    ]);
    expect(node.textContent).not.toContain("ربط متغير باختيار العميل");
});

test("removing one choice mapping preserves the other piece and text customization", async () => {
    const changed = jest.fn();
    await act(async () => root.render(<Editor initial={{ quantity: "2", slots: [
        { slot_id: "1", selection_option_ids: ["first_size", "first_color"], customization_option_ids: ["note"] },
        { slot_id: "2", selection_option_ids: ["second_size", "second_color"] },
    ] }} onChange={changed} />));
    await act(async () => choiceBox(1, "لون القطعة الأولى").click());
    expect(changed.mock.calls.at(-1)[0].slots).toEqual([
        { slot_id: "1", selection_option_ids: ["first_size"], customization_option_ids: ["note"] },
        { slot_id: "2", selection_option_ids: ["second_size", "second_color"] },
    ]);
    const choiceGroups = [...node.querySelectorAll("fieldset")].filter((fieldset) =>
        fieldset.querySelector(":scope > legend")?.textContent?.startsWith("اختيارات القطعة"));
    expect(choiceGroups).toHaveLength(2);
    expect(choiceGroups.every((fieldset) => !fieldset.textContent.includes("ملاحظة المورد"))).toBe(true);
});

test("quantity resizing keeps each saved piece mapping", () => {
    const slots = [{ slot_id: "1", selection_option_ids: ["first_size"] },
        { slot_id: "2", selection_option_ids: ["second_size"], customization_option_ids: ["note"] }];
    expect(resizeCurrentSlots(slots, 3)).toEqual([...slots, { slot_id: "3" }]);
});
