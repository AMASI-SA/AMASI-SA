import { useEffect, useMemo, useState } from "react";
import {
  ArrowClockwise,
  CheckCircle,
  ClipboardText,
  CurrencyCircleDollar,
  Printer,
  SpinnerGap,
  Truck,
  WarningCircle,
} from "@phosphor-icons/react";
import {
  approveReturnCase,
  createReturnCase,
  getReturnWorkspace,
  inspectReturnCase,
} from "../../services/returnDecisionEngine";

const REASONS = [
  ["defective", "عيب في القطعة"],
  ["wrong_item", "قطعة خاطئة"],
  ["shipping_damage", "تلف أثناء الشحن"],
  ["customer_changed_mind", "تراجع العميل"],
  ["size_or_fit", "المقاس أو الملاءمة"],
  ["other", "سبب آخر"],
];

const RESOLUTIONS = [
  ["either", "اعرض كل البدائل"],
  ["refund", "استرداد مبلغ"],
  ["replacement", "قطعة بديلة"],
];

const STATUS_LABELS = {
  draft: "مسودة بانتظار قرار الموظف",
  approved: "معتمد بانتظار تنفيذ الإجراء",
  label_issued: "تم إصدار بوليصة الإرجاع",
  in_transit: "المرتجع في الطريق",
  received: "وصل المرتجع وبانتظار الفحص",
  inspected: "تم فحص المرتجع",
  refund_pending: "الاسترداد المالي قيد التنفيذ",
  completed: "مكتمل",
  rejected: "مرفوض",
  cancelled: "ملغي",
};

const GATE_LABELS = {
  requires_employee_approval: "بانتظار اعتماد الموظف",
  ready_for_employee_execution: "جاهز لتنفيذ الموظف",
  not_required_customer_keeps_item: "غير مطلوب؛ القطعة تبقى مع العميل",
  blocked_until_inspection: "متوقف حتى الفحص",
  blocked_until_accepted_return: "متوقف حتى قبول المرتجع",
  blocked_until_settled_transaction: "متوقف حتى وجود عملية دفع مسوّاة",
  ready_for_sellable_quantity_movement: "جاهز لحركة مخزون الكمية الصالحة",
  no_sellable_inventory: "لا توجد كمية صالحة للمخزون",
  ready_for_financial_review: "جاهز للمراجعة المالية",
  not_applicable: "غير منطبق",
  not_required: "غير مطلوب",
};

const OPTION_LABELS = {
  return_refund: "استرجاع القطعة ورد المبلغ",
  keep_refund: "ترك القطعة مع العميل ورد المبلغ",
  return_replace: "استرجاع القطعة وإرسال بديل",
  keep_replace: "ترك القطعة مع العميل وإرسال بديل",
  keep_partial_refund: "ترك القطعة مع العميل وتعويض جزئي",
};

function money(value, currency) {
  return new Intl.NumberFormat("ar-SA-u-nu-latn", {
    style: "currency",
    currency: String(currency || "SAR").toUpperCase(),
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value || 0));
}

function numberOrNull(value) {
  if (value === "" || value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function itemId(item, index) {
  return String(item.order_item_id || item.id || `line-${index}`);
}

function labelUrl(label) {
  if (typeof label === "string") return label.trim();
  if (!label || typeof label !== "object") return "";
  return String(
    label.url || label.label_url || label.download_url || "",
  ).trim();
}

function Field({ label, children }) {
  return (
    <label className="space-y-1 text-sm font-bold text-slate-600">
      <span>{label}</span>
      {children}
    </label>
  );
}

const inputClass =
  "w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:border-violet-400";

export default function ReturnDecisionCard({
  orderNumber,
  items,
  currency,
  itemsLoading,
}) {
  const [workspace, setWorkspace] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState({});
  const [form, setForm] = useState({
    reason_code: "customer_changed_mind",
    requested_resolution: "either",
    refund_amount: "",
    partial_refund_amount: "",
    return_shipping_quote: "",
    customer_return_shipping_charge: "0",
    inspection_handling_cost: "",
    replacement_item_cost: "",
    replacement_shipping_cost: "",
    refund_processing_fee: "0",
    merchant_fault: false,
    legal_or_policy_return_required: false,
    notes: "",
    shipment_id: "",
  });
  const [approval, setApproval] = useState({
    selected_option: "",
    employee_note: "",
  });
  const [inspection, setInspection] = useState({});
  const [inspectionNote, setInspectionNote] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const value = await getReturnWorkspace(orderNumber);
      setWorkspace(value);
      if (!form.shipment_id && value.shipments.length) {
        setForm((current) => ({
          ...current,
          shipment_id: String(value.shipments[0].shipment_id || ""),
        }));
      }
    } catch (loadError) {
      setError(loadError.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // Opening the order reads Mezan only; this component never resyncs Salla.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderNumber]);

  const latestCase = workspace?.cases?.[0] || null;
  const shipments = workspace?.shipments || [];
  const selectedShipment =
    shipments.find(
      (shipment) => String(shipment.shipment_id || "") === form.shipment_id,
    ) || null;

  const chosenItems = useMemo(
    () =>
      items
        .map((item, index) => ({
          item,
          index,
          id: itemId(item, index),
          selection: selected[itemId(item, index)],
        }))
        .filter((row) => row.selection),
    [items, selected],
  );

  const toggleItem = (item, index) => {
    const id = itemId(item, index);
    setSelected((current) => {
      const next = { ...current };
      if (next[id]) delete next[id];
      else
        next[id] = {
          quantity_return: 1,
          unit_cost: "",
          expected_recoverable_value: "",
          sellable_probability: "1",
          refurbishment_cost_per_unit: "0",
        };
      return next;
    });
  };

  const updateSelection = (id, key, value) =>
    setSelected((current) => ({
      ...current,
      [id]: { ...current[id], [key]: value },
    }));

  const submitCase = async (event) => {
    event.preventDefault();
    if (!chosenItems.length) {
      setError("اختر قطعة واحدة على الأقل وحدد كمية الإرجاع.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const payloadItems = chosenItems.map(({ item, id, selection }) => {
        const ordered = Math.max(1, Math.trunc(Number(item.quantity || 1)));
        const lineTax = Number(item.tax_reported_by_source || item.tax || 0);
        return {
          order_item_id: id,
          product_id: item.product_id ? String(item.product_id) : null,
          sku: item.sku || null,
          name: item.name || null,
          quantity_ordered: ordered,
          quantity_return: Math.max(
            1,
            Math.min(
              ordered,
              Math.trunc(Number(selection.quantity_return || 1)),
            ),
          ),
          unit_sale_amount: Number(item.unit_price || 0),
          unit_tax_amount: ordered ? lineTax / ordered : 0,
          unit_cost: numberOrNull(selection.unit_cost),
          expected_recoverable_value: numberOrNull(
            selection.expected_recoverable_value,
          ),
          sellable_probability: Math.max(
            0,
            Math.min(1, Number(selection.sellable_probability || 0)),
          ),
          refurbishment_cost_per_unit:
            numberOrNull(selection.refurbishment_cost_per_unit) || 0,
        };
      });
      await createReturnCase(orderNumber, {
        currency,
        reason_code: form.reason_code,
        requested_resolution: form.requested_resolution,
        items: payloadItems,
        refund_amount: numberOrNull(form.refund_amount),
        partial_refund_amount: numberOrNull(form.partial_refund_amount),
        return_shipping_quote: numberOrNull(form.return_shipping_quote),
        customer_return_shipping_charge:
          numberOrNull(form.customer_return_shipping_charge) || 0,
        inspection_handling_cost: numberOrNull(form.inspection_handling_cost),
        replacement_item_cost: numberOrNull(form.replacement_item_cost),
        replacement_shipping_cost: numberOrNull(form.replacement_shipping_cost),
        refund_processing_fee: numberOrNull(form.refund_processing_fee) || 0,
        merchant_fault: form.merchant_fault,
        legal_or_policy_return_required: form.legal_or_policy_return_required,
        notes: form.notes.trim() || null,
        source_return_shipment_id: selectedShipment?.shipment_id
          ? String(selectedShipment.shipment_id)
          : null,
        source_return_tracking_number:
          selectedShipment?.tracking_number || null,
        source_return_label_url: labelUrl(selectedShipment?.label) || null,
        idempotency_key: `${orderNumber}:${Date.now()}`,
      });
      await load();
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  const submitApproval = async (event) => {
    event.preventDefault();
    if (!approval.selected_option) return setError("اختر قرار الموظف.");
    if (approval.employee_note.trim().length < 3)
      return setError("اكتب سبب القرار للرجوع إليه في التدقيق.");
    setSaving(true);
    setError("");
    try {
      await approveReturnCase(latestCase.id, {
        selected_option: approval.selected_option,
        expected_version: latestCase.version,
        employee_note: approval.employee_note.trim(),
      });
      await load();
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  const updateInspection = (id, key, value) =>
    setInspection((current) => ({
      ...current,
      [id]: { ...(current[id] || {}), [key]: value },
    }));

  const submitInspection = async (event) => {
    event.preventDefault();
    if (inspectionNote.trim().length < 3)
      return setError("اكتب ملاحظة الفحص والاستلام.");
    setSaving(true);
    setError("");
    try {
      await inspectReturnCase(latestCase.id, {
        expected_version: latestCase.version,
        items: (latestCase.selected_items || []).map((item) => {
          const values = inspection[item.order_item_id] || {};
          return {
            order_item_id: item.order_item_id,
            received_quantity: Math.max(
              0,
              Math.trunc(Number(values.received_quantity || 0)),
            ),
            accepted_quantity: Math.max(
              0,
              Math.trunc(Number(values.accepted_quantity || 0)),
            ),
            sellable_quantity: Math.max(
              0,
              Math.trunc(Number(values.sellable_quantity || 0)),
            ),
            damaged_quantity: Math.max(
              0,
              Math.trunc(Number(values.damaged_quantity || 0)),
            ),
            note: String(values.note || "").trim() || null,
          };
        }),
        employee_note: inspectionNote.trim(),
      });
      await load();
    } catch (saveError) {
      setError(saveError.message);
    } finally {
      setSaving(false);
    }
  };

  const startAnother = () => {
    setWorkspace((current) => (current ? { ...current, cases: [] } : current));
    setSelected({});
    setApproval({ selected_option: "", employee_note: "" });
    setInspection({});
    setInspectionNote("");
    setError("");
  };

  return (
    <section
      className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
      data-testid="return-decision-card"
    >
      <div className="flex flex-col gap-3 border-b border-slate-100 pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-start gap-3">
          <div className="rounded-xl bg-amber-100 p-2 text-amber-700">
            <ArrowClockwise size={23} weight="bold" />
          </div>
          <div>
            <h2 className="text-xl font-extrabold text-slate-950">
              محرك المرتجع والبدائل
            </h2>
            <p className="mt-1 text-sm leading-6 text-slate-500">
              اختيار القطع والكميات في ميزان هو المصدر المعتمد للمحاسبة
              والمخزون.
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading || saving}
          className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm font-bold text-slate-700 disabled:opacity-50"
        >
          <ArrowClockwise size={17} /> تحديث محلي
        </button>
      </div>

      <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-7 text-amber-900">
        <div className="flex items-center gap-2 font-extrabold">
          <WarningCircle size={21} weight="fill" /> قاعدة لا تقبل الاستثناء
        </div>
        <p className="mt-1">
          سلة قد تعرض جميع قطع الطلب داخل بوليصة الإرجاع؛ لذلك البوليصة مرجع شحن
          فقط، ولا تحدد ما أعاده العميل.
        </p>
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-700">
          {error}
        </div>
      )}
      {(loading || itemsLoading) && (
        <div className="flex min-h-32 items-center justify-center">
          <SpinnerGap size={29} className="animate-spin text-violet-600" />
        </div>
      )}

      {!loading && shipments.length > 0 && (
        <div className="mt-4 rounded-xl border border-sky-200 bg-sky-50 p-4">
          <div className="flex items-center gap-2 font-extrabold text-sky-900">
            <Truck size={21} weight="fill" /> بوليصات إرجاع مكتشفة من سلة
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {shipments.map((shipment) => {
              const url = labelUrl(shipment.label);
              return (
                <div
                  key={shipment.shipment_id || shipment.tracking_number}
                  className="rounded-lg bg-white p-3 text-sm text-slate-700"
                >
                  <div>
                    الشركة: <b>{shipment.courier_name || "—"}</b>
                  </div>
                  <div className="mt-1">
                    رقم التتبع:{" "}
                    <b className="num">{shipment.tracking_number || "—"}</b>
                  </div>
                  <div className="mt-1 text-xs font-bold text-amber-700">
                    عدد الحزم في سلة لا يمثل عدد القطع المرتجعة.
                  </div>
                  {url && (
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-2 inline-flex items-center gap-1 font-bold text-sky-700"
                    >
                      <Printer size={17} /> فتح البوليصة
                    </a>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {!loading && !latestCase && (
        <form onSubmit={submitCase} className="mt-5 space-y-5">
          <div>
            <h3 className="font-extrabold text-slate-900">
              1. اختر القطع التي قال العميل إنه سيعيدها
            </h3>
            <p className="mt-1 text-xs text-slate-500">
              لا ننسخ اختيار القطع من حزم بوليصة سلة.
            </p>
          </div>
          <div className="space-y-3">
            {items.map((item, index) => {
              const id = itemId(item, index);
              const value = selected[id];
              const ordered = Math.max(
                1,
                Math.trunc(Number(item.quantity || 1)),
              );
              return (
                <div
                  key={id}
                  className={`rounded-xl border p-4 ${value ? "border-violet-300 bg-violet-50/40" : "border-slate-200"}`}
                >
                  <label className="flex cursor-pointer items-start gap-3">
                    <input
                      type="checkbox"
                      checked={Boolean(value)}
                      onChange={() => toggleItem(item, index)}
                      className="mt-1 h-4 w-4 accent-violet-600"
                    />
                    <span className="flex-1">
                      <b className="text-slate-900">
                        {item.name || `قطعة ${index + 1}`}
                      </b>
                      <span className="mt-1 block text-xs text-slate-500">
                        SKU: {item.sku || "—"} · الكمية الأصلية:{" "}
                        <span className="num">{ordered}</span>
                      </span>
                    </span>
                  </label>
                  {value && (
                    <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                      <Field label="كمية الإرجاع">
                        <input
                          type="number"
                          min="1"
                          max={ordered}
                          value={value.quantity_return}
                          onChange={(event) =>
                            updateSelection(
                              id,
                              "quantity_return",
                              event.target.value,
                            )
                          }
                          className={inputClass}
                        />
                      </Field>
                      <Field label="تكلفة الوحدة">
                        <input
                          type="number"
                          min="0"
                          step="0.01"
                          value={value.unit_cost}
                          onChange={(event) =>
                            updateSelection(id, "unit_cost", event.target.value)
                          }
                          className={inputClass}
                          placeholder="اختياري"
                        />
                      </Field>
                      <Field label="قيمة الاستفادة المتوقعة">
                        <input
                          type="number"
                          min="0"
                          step="0.01"
                          value={value.expected_recoverable_value}
                          onChange={(event) =>
                            updateSelection(
                              id,
                              "expected_recoverable_value",
                              event.target.value,
                            )
                          }
                          className={inputClass}
                          placeholder="بعد الفحص"
                        />
                      </Field>
                      <Field label="احتمال صلاحيتها">
                        <input
                          type="number"
                          min="0"
                          max="1"
                          step="0.05"
                          value={value.sellable_probability}
                          onChange={(event) =>
                            updateSelection(
                              id,
                              "sellable_probability",
                              event.target.value,
                            )
                          }
                          className={inputClass}
                        />
                      </Field>
                      <Field label="تكلفة التجهيز للوحدة">
                        <input
                          type="number"
                          min="0"
                          step="0.01"
                          value={value.refurbishment_cost_per_unit}
                          onChange={(event) =>
                            updateSelection(
                              id,
                              "refurbishment_cost_per_unit",
                              event.target.value,
                            )
                          }
                          className={inputClass}
                        />
                      </Field>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div>
            <h3 className="font-extrabold text-slate-900">
              2. بيانات القرار الاقتصادي
            </h3>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="سبب المرتجع">
                <select
                  value={form.reason_code}
                  onChange={(event) =>
                    setForm({ ...form, reason_code: event.target.value })
                  }
                  className={inputClass}
                >
                  {REASONS.map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="طلب العميل">
                <select
                  value={form.requested_resolution}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      requested_resolution: event.target.value,
                    })
                  }
                  className={inputClass}
                >
                  {RESOLUTIONS.map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="تكلفة شحن الإرجاع">
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.return_shipping_quote}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      return_shipping_quote: event.target.value,
                    })
                  }
                  className={inputClass}
                  placeholder="عرض شركة الشحن"
                />
              </Field>
              <Field label="تكلفة الفحص والتجهيز">
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.inspection_handling_cost}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      inspection_handling_cost: event.target.value,
                    })
                  }
                  className={inputClass}
                />
              </Field>
              <Field label="تكلفة القطعة البديلة">
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.replacement_item_cost}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      replacement_item_cost: event.target.value,
                    })
                  }
                  className={inputClass}
                />
              </Field>
              <Field label="شحن البديل">
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.replacement_shipping_cost}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      replacement_shipping_cost: event.target.value,
                    })
                  }
                  className={inputClass}
                />
              </Field>
              <Field label="مبلغ الاسترداد">
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.refund_amount}
                  onChange={(event) =>
                    setForm({ ...form, refund_amount: event.target.value })
                  }
                  className={inputClass}
                  placeholder="تلقائي من القطع"
                />
              </Field>
              <Field label="تعويض جزئي محتمل">
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.partial_refund_amount}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      partial_refund_amount: event.target.value,
                    })
                  }
                  className={inputClass}
                />
              </Field>
              <Field label="المحمّل على العميل من الشحن">
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.customer_return_shipping_charge}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      customer_return_shipping_charge: event.target.value,
                    })
                  }
                  className={inputClass}
                />
              </Field>
              <Field label="رسوم تنفيذ الاسترداد">
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={form.refund_processing_fee}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      refund_processing_fee: event.target.value,
                    })
                  }
                  className={inputClass}
                />
              </Field>
              {shipments.length > 0 && (
                <Field label="بوليصة سلة المرتبطة">
                  <select
                    value={form.shipment_id}
                    onChange={(event) =>
                      setForm({ ...form, shipment_id: event.target.value })
                    }
                    className={inputClass}
                  >
                    <option value="">بدون ربط</option>
                    {shipments.map((shipment) => (
                      <option
                        key={shipment.shipment_id || shipment.tracking_number}
                        value={shipment.shipment_id || ""}
                      >
                        {shipment.tracking_number || shipment.shipment_id}
                      </option>
                    ))}
                  </select>
                </Field>
              )}
              <Field label="ملاحظات">
                <input
                  value={form.notes}
                  onChange={(event) =>
                    setForm({ ...form, notes: event.target.value })
                  }
                  className={inputClass}
                />
              </Field>
            </div>
          </div>

          <div className="flex flex-wrap gap-4 rounded-xl bg-slate-50 p-4 text-sm font-bold text-slate-700">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.merchant_fault}
                onChange={(event) =>
                  setForm({ ...form, merchant_fault: event.target.checked })
                }
                className="h-4 w-4 accent-violet-600"
              />{" "}
              خطأ من المتجر أو المنتج
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={form.legal_or_policy_return_required}
                onChange={(event) =>
                  setForm({
                    ...form,
                    legal_or_policy_return_required: event.target.checked,
                  })
                }
                className="h-4 w-4 accent-violet-600"
              />{" "}
              الإرجاع إلزامي بالنظام أو السياسة
            </label>
          </div>
          <button
            type="submit"
            disabled={saving || itemsLoading}
            className="inline-flex items-center gap-2 rounded-xl bg-violet-700 px-5 py-3 font-extrabold text-white disabled:opacity-50"
          >
            {saving ? (
              <SpinnerGap size={19} className="animate-spin" />
            ) : (
              <CurrencyCircleDollar size={21} weight="fill" />
            )}{" "}
            إنشاء تقرير القرار
          </button>
        </form>
      )}

      {!loading && latestCase && (
        <div className="mt-5 space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="font-extrabold text-slate-950">
                حالة المرتجع:{" "}
                {STATUS_LABELS[latestCase.status] || latestCase.status}
              </h3>
              <p className="mt-1 text-xs text-slate-500">
                الإصدار <span className="num">{latestCase.version}</span> ·
                القطع المختارة يدويًا:{" "}
                <span className="num">
                  {latestCase.decision_report?.selected_quantity || 0}
                </span>
              </p>
            </div>
            {latestCase.status === "inspected" && (
              <button
                type="button"
                onClick={startAnother}
                className="rounded-xl border border-violet-200 px-3 py-2 text-sm font-bold text-violet-700"
              >
                بدء مرتجع آخر
              </button>
            )}
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl bg-slate-50 p-4">
              <div className="text-xs font-bold text-slate-400">
                قيمة القطع المختارة
              </div>
              <div className="num mt-2 font-extrabold">
                {money(
                  latestCase.decision_report?.selected_sale_value,
                  currency,
                )}
              </div>
            </div>
            <div className="rounded-xl bg-slate-50 p-4">
              <div className="text-xs font-bold text-slate-400">
                تكلفة استعادة القطعة
              </div>
              <div className="num mt-2 font-extrabold">
                {money(latestCase.decision_report?.recovery_cost, currency)}
              </div>
            </div>
            <div className="rounded-xl bg-slate-50 p-4">
              <div className="text-xs font-bold text-slate-400">
                صافي فائدة الاستعادة
              </div>
              <div className="num mt-2 font-extrabold">
                {money(
                  latestCase.decision_report?.retrieval_net_benefit,
                  currency,
                )}
              </div>
            </div>
            <div className="rounded-xl bg-violet-50 p-4">
              <div className="text-xs font-bold text-violet-500">
                توصية المحرك
              </div>
              <div className="mt-2 font-extrabold text-violet-900">
                {OPTION_LABELS[
                  latestCase.decision_report?.recommended_option
                ] || "—"}
              </div>
            </div>
          </div>

          {latestCase.decision_report?.missing_inputs?.length > 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              <b>
                ثقة التقرير{" "}
                {latestCase.decision_report.confidence === "low"
                  ? "منخفضة"
                  : "متوسطة"}
                :
              </b>{" "}
              توجد مدخلات ناقصة، فلا تعتمد التوصية وحدها.
            </div>
          )}

          <div className="grid gap-3 lg:grid-cols-2">
            {(latestCase.decision_report?.options || []).map((option) => (
              <label
                key={option.key}
                className={`rounded-xl border p-4 ${option.available ? "cursor-pointer border-slate-200" : "border-slate-100 bg-slate-50 opacity-50"}`}
              >
                <div className="flex items-start gap-3">
                  <input
                    type="radio"
                    name="return-option"
                    disabled={
                      !option.available || latestCase.status !== "draft"
                    }
                    checked={approval.selected_option === option.key}
                    onChange={() =>
                      setApproval({ ...approval, selected_option: option.key })
                    }
                    className="mt-1 h-4 w-4 accent-violet-600"
                  />
                  <div className="flex-1">
                    <div className="font-extrabold text-slate-900">
                      {option.label}
                    </div>
                    <div className="num mt-1 text-sm font-bold text-slate-600">
                      التكلفة الإضافية المتوقعة:{" "}
                      {money(option.incremental_cost, currency)}
                    </div>
                    <ul className="mt-2 list-inside list-disc text-xs leading-6 text-slate-500">
                      {(option.reasons || []).map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </label>
            ))}
          </div>

          {latestCase.status === "draft" && (
            <form
              onSubmit={submitApproval}
              className="rounded-xl border border-violet-200 bg-violet-50 p-4"
            >
              <h4 className="font-extrabold text-violet-950">3. قرار الموظف</h4>
              <textarea
                value={approval.employee_note}
                onChange={(event) =>
                  setApproval({
                    ...approval,
                    employee_note: event.target.value,
                  })
                }
                className={`${inputClass} mt-3 min-h-24`}
                placeholder="اكتب سبب اختيار القرار والملاحظات التشغيلية"
              />
              <button
                type="submit"
                disabled={saving}
                className="mt-3 inline-flex items-center gap-2 rounded-xl bg-violet-700 px-5 py-3 font-extrabold text-white disabled:opacity-50"
              >
                {saving ? (
                  <SpinnerGap size={19} className="animate-spin" />
                ) : (
                  <CheckCircle size={21} weight="fill" />
                )}{" "}
                اعتماد القرار
              </button>
            </form>
          )}

          {latestCase.status !== "draft" && (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
              <div className="font-extrabold">
                القرار المعتمد:{" "}
                {OPTION_LABELS[latestCase.approval?.selected_option] ||
                  latestCase.approval?.selected_option}
              </div>
              <p className="mt-1">{latestCase.approval?.employee_note}</p>
            </div>
          )}

          {["approved", "label_issued", "in_transit", "received"].includes(
            latestCase.status,
          ) &&
            latestCase.decision_report?.options?.find(
              (option) => option.key === latestCase.approval?.selected_option,
            )?.retrieves_item && (
              <form
                onSubmit={submitInspection}
                className="rounded-xl border border-sky-200 bg-sky-50 p-4"
              >
                <div className="flex items-center gap-2">
                  <ClipboardText
                    size={21}
                    weight="fill"
                    className="text-sky-700"
                  />
                  <h4 className="font-extrabold text-sky-950">
                    4. فحص ما وصل فعليًا
                  </h4>
                </div>
                <p className="mt-1 text-xs text-sky-800">
                  سجل كل قطعة مختارة؛ اكتب صفرًا للقطعة التي لم تصل.
                </p>
                <div className="mt-4 space-y-3">
                  {(latestCase.selected_items || []).map((item) => {
                    const values = inspection[item.order_item_id] || {};
                    return (
                      <div
                        key={item.order_item_id}
                        className="rounded-xl bg-white p-4"
                      >
                        <div className="font-extrabold">
                          {item.name || item.sku || item.order_item_id} · معتمد
                          للإرجاع:{" "}
                          <span className="num">{item.quantity_return}</span>
                        </div>
                        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                          <Field label="وصل">
                            <input
                              type="number"
                              min="0"
                              max={item.quantity_return}
                              value={values.received_quantity || ""}
                              onChange={(event) =>
                                updateInspection(
                                  item.order_item_id,
                                  "received_quantity",
                                  event.target.value,
                                )
                              }
                              className={inputClass}
                            />
                          </Field>
                          <Field label="مقبول">
                            <input
                              type="number"
                              min="0"
                              max={item.quantity_return}
                              value={values.accepted_quantity || ""}
                              onChange={(event) =>
                                updateInspection(
                                  item.order_item_id,
                                  "accepted_quantity",
                                  event.target.value,
                                )
                              }
                              className={inputClass}
                            />
                          </Field>
                          <Field label="صالح للبيع">
                            <input
                              type="number"
                              min="0"
                              max={item.quantity_return}
                              value={values.sellable_quantity || ""}
                              onChange={(event) =>
                                updateInspection(
                                  item.order_item_id,
                                  "sellable_quantity",
                                  event.target.value,
                                )
                              }
                              className={inputClass}
                            />
                          </Field>
                          <Field label="تالف">
                            <input
                              type="number"
                              min="0"
                              max={item.quantity_return}
                              value={values.damaged_quantity || ""}
                              onChange={(event) =>
                                updateInspection(
                                  item.order_item_id,
                                  "damaged_quantity",
                                  event.target.value,
                                )
                              }
                              className={inputClass}
                            />
                          </Field>
                          <Field label="ملاحظة القطعة">
                            <input
                              value={values.note || ""}
                              onChange={(event) =>
                                updateInspection(
                                  item.order_item_id,
                                  "note",
                                  event.target.value,
                                )
                              }
                              className={inputClass}
                            />
                          </Field>
                        </div>
                      </div>
                    );
                  })}
                </div>
                <textarea
                  value={inspectionNote}
                  onChange={(event) => setInspectionNote(event.target.value)}
                  className={`${inputClass} mt-3 min-h-20`}
                  placeholder="ملاحظة الاستلام والفحص"
                />
                <button
                  type="submit"
                  disabled={saving}
                  className="mt-3 inline-flex items-center gap-2 rounded-xl bg-sky-700 px-5 py-3 font-extrabold text-white disabled:opacity-50"
                >
                  {saving ? (
                    <SpinnerGap size={19} className="animate-spin" />
                  ) : (
                    <ClipboardText size={21} weight="fill" />
                  )}{" "}
                  حفظ الفحص
                </button>
              </form>
            )}

          <div>
            <h4 className="font-extrabold text-slate-900">بوابات التنفيذ</h4>
            <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {Object.entries(latestCase.execution_gates || {}).map(
                ([key, value]) => (
                  <div
                    key={key}
                    className="rounded-lg border border-slate-200 p-3 text-xs"
                  >
                    <div className="font-bold text-slate-400">
                      {key === "return_label"
                        ? "بوليصة الإرجاع"
                        : key === "inventory"
                          ? "المخزون"
                          : key === "credit_note"
                            ? "الإشعار الدائن"
                            : "رد المبلغ"}
                    </div>
                    <div className="mt-1 font-extrabold text-slate-800">
                      {GATE_LABELS[value] || value}
                    </div>
                  </div>
                ),
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
