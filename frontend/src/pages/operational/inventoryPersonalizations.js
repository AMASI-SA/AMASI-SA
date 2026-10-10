// Product-defined fields distribute an existing variant quantity, never new stock.
export function inventoryPersonalizations(line) {
  const rows = line.personalizations || [];
  if (!rows.length) return {};
  const fields = line.customization_fields || [];
  if (line.kind !== 'product' || rows.length > 100 || !fields.length || line.customization_issues?.length) throw Error('خيارات تخصيص المنتج غير متاحة أو غير مكتملة في ميزان 2.');
  const seen = new Set();
  let assigned = 0;
  const personalizations = rows.map(row => {
    if (!Array.isArray(row.values) || row.values.length > fields.length || new Set(row.values.map(v=>v.option_id)).size !== row.values.length || row.values.some(v=>!fields.some(f=>f.id===v.option_id))) throw Error('خيارات التخصيص غير مطابقة للمنتج.');
    const values = fields.map(field=>{
      const raw = row.values.find(v=>v.option_id===field.id)?.value ?? '';
      if(typeof raw!=='string' || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(raw)) throw Error('أدخل قيمة نصية صالحة.');
      const value=raw.normalize('NFC').trim().replace(/\s+/gu,' ');
      if((field.required&&!value)||Array.from(value).length>(field.type==='textarea'?1000:100)) throw Error(`أكمل ${field.name} بقيمة ضمن الحد المسموح.`);
      return {option_id:field.id,value};
    }).filter(v=>v.value);
    if(!values.length) throw Error('أدخل خيار تخصيص واحدًا على الأقل.');
    const quantity = Number(row.quantity);
    if (!Number.isSafeInteger(quantity) || quantity <= 0) throw Error('كمية التخصيص يجب أن تكون عددًا صحيحًا موجبًا.');
    const key = JSON.stringify(values.map(v=>[v.option_id,v.value.toLowerCase()]));
    if (seen.has(key)) throw Error('التخصيص مكرر داخل الخيار نفسه؛ اجمع كميته في سطر واحد.');
    seen.add(key);
    assigned += quantity;
    return {values, quantity};
  });
  if (!Number.isSafeInteger(assigned) || assigned > Number(line.quantity)) throw Error('توزيع التخصيصات يتجاوز كمية المنتج لهذا الخيار.');
  return {personalizations};
}

export const assignedQuantity = line => (line.personalizations || []).reduce((sum, row) => sum + (Number(row.quantity) || 0), 0);
