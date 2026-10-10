// Names distribute the already purchased variant quantity; they are not new stock lines.
export function inventoryPersonalizations(line) {
  const rows = line.personalizations || [];
  if (!rows.length) return {};
  if (line.kind !== 'product' || rows.length > 100) throw Error('توزيع الأسماء للمنتجات فقط وبحد أقصى 100 اسم لكل خيار.');
  const seen = new Set();
  let assigned = 0;
  const personalizations = rows.map(row => {
    const name = String(row.name || '').normalize('NFC').trim().replace(/\s+/gu, ' ');
    const quantity = Number(row.quantity);
    if (!name || Array.from(name).length > 100) throw Error('أدخل اسمًا من 1 إلى 100 حرف لكل توزيع.');
    if (!Number.isSafeInteger(quantity) || quantity <= 0) throw Error('كمية الاسم يجب أن تكون عددًا صحيحًا موجبًا.');
    const key = name.toLowerCase();
    if (seen.has(key)) throw Error('الاسم مكرر داخل الخيار نفسه؛ اجمع كميته في سطر واحد.');
    seen.add(key);
    assigned += quantity;
    return {name, quantity};
  });
  if (!Number.isSafeInteger(assigned) || assigned > Number(line.quantity)) throw Error('توزيع الأسماء يتجاوز كمية المنتج لهذا الخيار.');
  return {personalizations};
}

export const assignedQuantity = line => (line.personalizations || []).reduce((sum, row) => sum + (Number(row.quantity) || 0), 0);
