import {
  canonicalReviewSpecKey,
  isInternalPreparationRoute,
  nextManualHiddenKeys,
} from "./reviewExportControlsEnhancer";
import {
  canSubmitResponsibleEmployee,
  findOperationalProductButton,
  internalPreparationActionLabel,
  responsibleEmployeeId,
  responsibleEmployeeName,
} from "./reviewInternalPreparationRouteEnhancer";


describe("review export controls", () => {
  test("canonicalizes review field names", () => {
    expect(canonicalReviewSpecKey("اللون:")).toBe("color");
    expect(canonicalReviewSpecKey(" اسحب وافلت الصورة هنا ")).toBe(
      "اسحب وافلت الصورة هنا",
    );
  });

  test("toggles one manually hidden field without deleting other fields", () => {
    expect(nextManualHiddenKeys([], "اسحب وافلت الصورة هنا")).toEqual([
      "اسحب وافلت الصورة هنا",
    ]);
    expect(
      nextManualHiddenKeys(
        ["الاسم", "اسحب وافلت الصورة هنا"],
        "اسحب وافلت الصورة هنا",
      ),
    ).toEqual(["الاسم"]);
  });

  test("recognizes items routed directly to internal preparation", () => {
    expect(isInternalPreparationRoute({
      preparation_route: "internal_preparation",
      supplier_export: false,
    })).toBe(true);
    expect(isInternalPreparationRoute({
      preparation_route: "supplier_file",
      supplier_export: true,
    })).toBe(false);
  });

  test("finds the operational product action after its visible label is compacted", () => {
    const card = document.createElement("article");
    card.innerHTML = `
      <div data-actions>
        <button
          type="button"
          data-review-full-label="إضافة منتج تشغيلي"
          aria-label="إضافة منتج تشغيلي"
        >منتج</button>
      </div>
    `;

    const button = findOperationalProductButton(card);
    expect(button).not.toBeNull();
    expect(button.textContent.trim()).toBe("منتج");
    expect(button.parentElement).toBe(card.querySelector("[data-actions]"));
  });

  test("uses compact labels for the internal preparation route action", () => {
    expect(internalPreparationActionLabel({
      preparation_route: "supplier_file",
      supplier_export: true,
    })).toBe("توجيه للتجهيز");
    expect(internalPreparationActionLabel({
      preparation_route: "internal_preparation",
      supplier_export: false,
    })).toBe("إرجاع للملف");
  });

  test("requires a responsible employee before internal preparation", () => {
    expect(canSubmitResponsibleEmployee("")).toBe(false);
    expect(canSubmitResponsibleEmployee(" employee-1 ")).toBe(true);
  });

  test("prefers the order assignment then falls back to the saved product default", () => {
    expect(responsibleEmployeeId({
      assigned_employee_id: "employee-order",
      default_assigned_employee_id: "employee-default",
    })).toBe("employee-order");
    expect(responsibleEmployeeId({
      assigned_employee_id: null,
      default_assigned_employee_id: "employee-default",
    })).toBe("employee-default");
  });

  test("shows the responsible employee name from the current assignment or employee list", () => {
    expect(responsibleEmployeeName({
      assigned_employee_id: "employee-1",
      assigned_employee_name: "موظف التغليف",
    }, [])).toBe("موظف التغليف");

    expect(responsibleEmployeeName({
      default_assigned_employee_id: "employee-2",
    }, [
      { id: "employee-2", name: "موظف الطباعة" },
    ])).toBe("موظف الطباعة");
  });
});
