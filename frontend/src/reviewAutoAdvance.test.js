import {
  armReviewAutoAdvance,
  attemptReviewAutoAdvance,
  clearPendingReviewAdvance,
  orderedNextReviewNumbers,
  pendingReviewOrderRows,
  reviewOrderNumberFromHeading,
} from "./reviewAutoAdvance";

function addPendingRow(orderNumber) {
  const button = document.createElement("button");
  button.type = "button";
  button.innerHTML = `<span dir="ltr">#${orderNumber}</span>`;
  document.body.appendChild(button);
  return button;
}

function addDrawer(orderNumber) {
  const section = document.createElement("section");
  section.innerHTML = `<h2>مراجعة الطلب #${orderNumber}</h2>`;
  document.body.appendChild(section);
  return section;
}

beforeEach(() => {
  document.body.innerHTML = "";
  clearPendingReviewAdvance();
  jest.useRealTimers();
});

test("orders the next row after the completed order and wraps to the first row", () => {
  expect(orderedNextReviewNumbers(["100", "99", "98"], "99"))
    .toEqual(["98", "100"]);
  expect(orderedNextReviewNumbers(["100", "99", "98"], "98"))
    .toEqual(["100", "99"]);
});

test("reads the active drawer and pending rows without matching unrelated buttons", () => {
  addDrawer("100");
  addPendingRow("100");
  addPendingRow("99");
  const unrelated = document.createElement("button");
  unrelated.textContent = "التالي";
  document.body.appendChild(unrelated);

  expect(reviewOrderNumberFromHeading()).toBe("100");
  expect(pendingReviewOrderRows().map((row) => row.orderNumber))
    .toEqual(["100", "99"]);
});

test("successful completion removes current row and opens the following order", () => {
  const drawer = addDrawer("100");
  const current = addPendingRow("100");
  const next = addPendingRow("99");
  const openNext = jest.fn();
  next.addEventListener("click", openNext);

  armReviewAutoAdvance("100");
  expect(attemptReviewAutoAdvance()).toBe(false);

  drawer.remove();
  current.remove();
  expect(attemptReviewAutoAdvance()).toBe(true);
  expect(openNext).toHaveBeenCalledTimes(1);
});

test("manually closing the drawer does not advance while completed row remains", () => {
  const drawer = addDrawer("100");
  addPendingRow("100");
  const next = addPendingRow("99");
  const openNext = jest.fn();
  next.addEventListener("click", openNext);

  armReviewAutoAdvance("100");
  drawer.remove();

  expect(attemptReviewAutoAdvance()).toBe(false);
  expect(openNext).not.toHaveBeenCalled();
});

test("when completed row was last, opens the first remaining pending order", () => {
  const first = addPendingRow("100");
  const second = addPendingRow("99");
  const drawer = addDrawer("99");
  const openFirst = jest.fn();
  first.addEventListener("click", openFirst);

  armReviewAutoAdvance("99");
  drawer.remove();
  second.remove();

  expect(attemptReviewAutoAdvance()).toBe(true);
  expect(openFirst).toHaveBeenCalledTimes(1);
});
