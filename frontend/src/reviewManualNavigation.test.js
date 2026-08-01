import {
  adjacentReviewOrder,
  decorateReviewManualNavigation,
  navigateReviewManually,
  reviewManualNavigationState,
} from "./reviewManualNavigation";

function addQueueRow(orderNumber, host = document.body) {
  const button = document.createElement("button");
  button.type = "button";
  button.innerHTML = `<span>#${orderNumber}</span>`;
  host.appendChild(button);
  return button;
}

function addDrawer(orderNumber) {
  const section = document.createElement("section");
  section.innerHTML = `
    <header>
      <div><h2>مراجعة الطلب #${orderNumber}</h2></div>
      <button type="button" aria-label="إغلاق">×</button>
    </header>
    <div class="complete-card">
      <p>اعتماد المراجعة يخرج الطلب من هذه الصفحة.</p>
      <button type="button">تمت المراجعة</button>
    </div>
  `;
  document.body.appendChild(section);
  return section;
}

beforeEach(() => {
  document.body.innerHTML = "";
});

test("resolves previous and next orders without wrapping manual navigation", () => {
  const rows = [
    { orderNumber: "100" },
    { orderNumber: "99" },
    { orderNumber: "98" },
  ];
  expect(adjacentReviewOrder(rows, "99", "previous")?.orderNumber).toBe("100");
  expect(adjacentReviewOrder(rows, "99", "next")?.orderNumber).toBe("98");
  expect(adjacentReviewOrder(rows, "100", "previous")).toBeNull();
  expect(adjacentReviewOrder(rows, "98", "next")).toBeNull();
});

test("renders synchronized arrows in the header and opposite the complete button", () => {
  const queue = document.createElement("section");
  queue.dataset.reviewQueueSection = "pending";
  document.body.appendChild(queue);
  addQueueRow("100", queue);
  addQueueRow("99", queue);
  addQueueRow("98", queue);
  addDrawer("99");

  const result = decorateReviewManualNavigation();
  expect(result.headerHost).not.toBeNull();
  expect(result.footerHost).not.toBeNull();
  expect(result.footerHost.parentElement.className).toContain("complete-card");
  expect(result.headerHost.querySelector('[data-review-navigation-direction="previous"]')?.dataset.targetOrderNumber).toBe("100");
  expect(result.footerHost.querySelector('[data-review-navigation-direction="next"]')?.dataset.targetOrderNumber).toBe("98");
  expect(result.state.currentPosition).toBeUndefined();
  expect(result.headerHost.dataset.currentPosition).toBe("2");
});

test("manual arrows open the selected adjacent order", () => {
  const queue = document.createElement("section");
  queue.dataset.reviewQueueSection = "pending";
  document.body.appendChild(queue);
  const previous = addQueueRow("100", queue);
  addQueueRow("99", queue);
  const next = addQueueRow("98", queue);
  addDrawer("99");
  const openPrevious = jest.fn();
  const openNext = jest.fn();
  previous.addEventListener("click", openPrevious);
  next.addEventListener("click", openNext);

  expect(reviewManualNavigationState().currentIndex).toBe(1);
  expect(navigateReviewManually("previous")).toBe("100");
  expect(openPrevious).toHaveBeenCalledTimes(1);
  expect(navigateReviewManually("next")).toBe("98");
  expect(openNext).toHaveBeenCalledTimes(1);
});
