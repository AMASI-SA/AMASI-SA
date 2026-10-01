// Date-only [start, end) allocation; BigInt cents keep money exact.
const day = value => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) throw Error("تاريخ غير صالح");
    const date = new Date(`${value}T00:00:00Z`);
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value) throw Error("تاريخ غير صالح");
    return date.getTime() / 86400000;
};
const money = cents => `${cents / 100n}.${String(cents % 100n).padStart(2, "0")}`;
export function calculatePrepaid(row, cutoverDate) {
    const start = day(row.coverage_start), end = day(row.coverage_end), cutoff = day(cutoverDate), paid = day(row.payment_date);
    if (end <= start) throw Error("نهاية التغطية يجب أن تلي بدايتها");
    if (paid >= cutoff) throw Error("يجب أن يكون الدفع قبل يوم القطع");
    if (end <= cutoff) throw Error("لا توجد تغطية متبقية بعد القطع");
    if (row.currency !== "SAR") throw Error("أكد العملة SAR؛ العملات الأخرى تحتاج عقد إثبات تحويل");
    if (!/^\d{1,15}(\.\d{1,2})?$/.test(row.amount_paid || "")) throw Error("أدخل المبلغ المدفوع بمنزلتين عشريتين كحد أقصى");
    const [whole, fraction = ""] = row.amount_paid.split(".");
    const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
    if (cents <= 0n) throw Error("المبلغ المدفوع يجب أن يكون موجبًا؛ الالتزام غير المدفوع ليس أصلًا");
    if (row.payment_status === "unpaid") throw Error("الالتزام غير مدفوع؛ لا يمكن تحويله إلى أصل مقدم");
    const days = end - start, consumedDays = Math.max(0, Math.min(days, cutoff - start));
    const consumed = (cents * BigInt(consumedDays) * 2n + BigInt(days)) / (2n * BigInt(days));
    return { coverage_days: days, consumed_days: consumedDays, remaining_days: days - consumedDays,
        consumed_before_cutover: money(consumed), prepaid_remaining_at_cutover: money(cents - consumed) };
}

export function validatePrepaidRow(row, cutoverDate, obligations = []) {
    const errors = [];
    try { calculatePrepaid(row, cutoverDate); } catch (error) { errors.push(error.message); }
    if (!row.title?.trim()) errors.push("اسم البند مطلوب");
    if (!row.evidence_ref?.trim()) errors.push("دليل الدفع والتغطية مطلوب");
    if (row.source_mode === "obligation") {
        const source = obligations.find(item => item.id === row.obligation_id);
        if (!source) errors.push("اختر التزامًا موجودًا في ميزان 2");
        if (source?.payment_status === "unpaid") errors.push("المصدر يثبت أن الالتزام غير مدفوع");
    } else if (row.source_mode !== "manual_exception" || !row.entity?.trim()) errors.push("حدد مصدر البند والجهة في الاستثناء اليدوي");
    return errors;
}
