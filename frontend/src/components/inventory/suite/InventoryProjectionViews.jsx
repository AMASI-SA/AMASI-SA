import { ArrowLeft, CheckCircle, Cube, WarningCircle, Warehouse, ChartBar, Plus, Receipt, ClipboardText } from "@phosphor-icons/react";

export const inventoryNumber = (value) => value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);
export const inventoryDisplay = (value) => {
  const number = inventoryNumber(value);
  return number === null ? "غير متاح" : number.toLocaleString("ar-SA", { maximumFractionDigits: 6 });
};
export const inventoryUnit = (value) => ({ piece: "قطعة", gram: "جرام", kg: "كيلوجرام", m: "متر", job: "خدمة" }[value] || value || "وحدة غير محددة");
export const stockSpecifications = (specifications) => Object.entries(specifications || {}).map(([name, value]) => name + ": " + value).join(" · ") || "بدون تخصيص";
export const rowNeedsReview = (row) => Boolean(row.availability_issues?.length) || inventoryNumber(row.available) === null || inventoryNumber(row.held) > 0;
const strictTotal = (rows, field) => rows.some((row) => inventoryNumber(row[field]) === null) ? null : rows.reduce((sum, row) => sum + inventoryNumber(row[field]), 0);

export function summarizeInventoryProjection(items = []) {
  const groups = new Map();
  items.forEach((row) => {
    const key = JSON.stringify([row.warehouse_id || row.warehouse_name || null, row.unit || null]);
    if (!groups.has(key)) groups.set(key, { key, warehouse_name: row.warehouse_name || "مستودع غير محدد", unit: row.unit, rows: [] });
    groups.get(key).rows.push(row);
  });
  return {
    recordCount: items.length,
    warehouseCount: new Set(items.map((row) => row.warehouse_id || row.warehouse_name).filter(Boolean)).size,
    unknownWarehouseRows: items.filter((row) => !row.warehouse_id && !row.warehouse_name).length,
    reviewRows: items.filter(rowNeedsReview),
    heldRecordCount: items.filter((row) => inventoryNumber(row.held) > 0).length,
    groups: [...groups.values()].map((group) => ({
      ...group,
      physical: strictTotal(group.rows, "physical"),
      reserved: strictTotal(group.rows, "reserved"),
      available: strictTotal(group.rows, "available"),
      held: strictTotal(group.rows, "held"),
      inventory_value: strictTotal(group.rows, "inventory_value"),
      unknownCostRows: group.rows.filter((row) => inventoryNumber(row.inventory_value) === null).length,
    })),
  };
}

export function InventoryProjectionMetrics({ summary }) {
  return <section className="mz-stock-metrics" aria-label="ملخص الأرصدة">
    {[["سجلات المخزون", summary.recordCount, Cube, "أرصدة منفصلة حسب الموقع والمواصفات"], ["المستودعات", summary.warehouseCount, Warehouse, summary.unknownWarehouseRows ? summary.unknownWarehouseRows + " سجل بمستودع غير محدد" : "المستودعات الواردة في الأرصدة"], ["تحتاج مراجعة", summary.reviewRows.length, WarningCircle, "توقف أو إتاحة غير محسومة"], ["أرصدة موقوفة", summary.heldRecordCount, ClipboardText, "عدد الأرصدة التي تتضمن كمية موقوفة"]].map(([label, value, Icon, hint]) => <article key={label}><div><span>{label}</span><strong>{inventoryDisplay(value)}</strong><small>{hint}</small></div><span className="mz-stock-metric-icon"><Icon size={24}/></span></article>)}
  </section>;
}

function ProjectionItem({ row }) {
  return <span className="mz-stock-item"><span className="mz-stock-item-icon">{row.main_image ? <img alt="" src={row.main_image}/> : <Cube size={23}/>}</span><span><strong>{row.name}</strong><small><bdi>{row.sku || (row.resource_id ? "مكوّن مخزني" : "بدون رقم مخزني")}</bdi> · {stockSpecifications(row.specifications)}</small></span></span>;
}

export function InventoryHomeProjection({ result, context, onNavigate }) {
  const items = result.items, summary = summarizeInventoryProjection(items);
  return <div className="mz-stock-home" data-testid="native-inventory-home">
    <InventoryProjectionMetrics summary={summary}/>
    <div className="mz-stock-home-grid">
      <section className="mz-stock-surface"><header className="mz-stock-section-heading"><div><small>الخطوة التالية</small><h2>أعمال تحتاج مراجعة</h2></div><span className="mz-stock-count">{summary.reviewRows.length}</span></header>
        {summary.reviewRows.length ? summary.reviewRows.slice(0, 6).map((row) => <button key={row.id} type="button" className="mz-stock-review-row" onClick={() => onNavigate?.("stock", row.id)}><span className="mz-stock-review-icon"><WarningCircle size={23}/></span><span><strong>{row.name}</strong><small>{row.warehouse_name || "مستودع غير محدد"} · {row.location_code || "خانة غير محددة"}</small><em>{row.availability_issues?.length ? "بيانات تحتاج مراجعة" : inventoryNumber(row.available) === null ? "الإتاحة غير مؤكدة" : "كمية موقوفة"}</em></span><ArrowLeft size={18}/></button>) : <div className="mz-stock-clear"><CheckCircle size={32}/><strong>لا توجد أرصدة تتطلب مراجعة في هذه القراءة</strong><p>يمكنك عرض الأرصدة والتكاليف حسب المستودع والموقع.</p></div>}
        <footer className="mz-stock-panel-footer"><span>الأعمال مستمدة من الأرصدة الفعلية المقروءة.</span><button type="button" onClick={() => onNavigate?.("stock")}>عرض المخزون <ArrowLeft size={15}/></button></footer>
      </section>
      <aside className="mz-stock-surface"><header className="mz-stock-section-heading"><h2>ابدأ من هنا</h2></header><div className="mz-stock-quick-actions">
        {context.can_manage_inventory_opening === true && <button type="button" onClick={() => onNavigate?.("opening")}><Plus size={24}/><span><strong>إضافة مخزون موجود</strong><small>الصنف، المواصفات، الكمية والتكلفة</small></span><ArrowLeft size={17}/></button>}
        {context.permissions?.reports === true && <button type="button" onClick={() => onNavigate?.("purchases")}><Receipt size={24}/><span><strong>مشتريات المخزون</strong><small>مراجعة الفواتير الحالية</small></span><ArrowLeft size={17}/></button>}
        <button type="button" onClick={() => onNavigate?.("reports")}><ChartBar size={24}/><span><strong>تقارير الأرصدة</strong><small>تجميع حسب المستودع ووحدة القياس</small></span><ArrowLeft size={17}/></button>
      </div><div className="mz-stock-source-note"><CheckCircle size={17}/>الكميات غير المثبتة تبقى غير متاحة.</div></aside>
    </div>
    <section className="mz-stock-surface"><header className="mz-stock-section-heading"><div><small>صورة المخزون</small><h2>المخزون في لمحة</h2></div><button type="button" onClick={() => onNavigate?.("stock")}>جميع الأرصدة <ArrowLeft size={15}/></button></header>
      {!items.length ? <div className="mz-stock-empty"><Cube size={42}/><h3>لا توجد أرصدة مخزنية ضمن النطاق</h3><p>ستظهر هنا الكميات المثبتة في مواقع التخزين.</p></div> : <div className="mz-stock-table-wrap"><table className="mz-stock-table"><thead><tr><th>الصنف والخيارات</th><th>الموقع</th><th>الفعلية</th><th>المحجوزة</th><th>المتاحة</th><th>الوحدة</th></tr></thead><tbody>{items.slice(0, 6).map((row) => <tr key={row.id}><th scope="row"><button type="button" onClick={() => onNavigate?.("stock", row.id)}><ProjectionItem row={row}/></button></th><td>{row.warehouse_name || "غير محدد"}<small>{row.location_code || "غير محدد"}</small></td><td>{inventoryDisplay(row.physical)}</td><td>{inventoryDisplay(row.reserved)}</td><td className="mz-stock-available">{inventoryDisplay(row.available)}</td><td>{inventoryUnit(row.unit)}</td></tr>)}</tbody></table></div>}
      <footer className="mz-stock-panel-footer"><span>عرض {Math.min(6, items.length)} من {items.length} سجلًا. لا تجمع وحدات قياس مختلفة.</span><button type="button" onClick={() => onNavigate?.("reports")}>تقارير المخزون <ArrowLeft size={15}/></button></footer>
    </section>
  </div>;
}

export function InventoryReportsProjection({ result }) {
  const summary = summarizeInventoryProjection(result.items);
  return <div className="mz-stock-reports" data-testid="native-inventory-reports">
    <InventoryProjectionMetrics summary={summary}/>
    <section className="mz-stock-surface"><header className="mz-stock-section-heading"><div><small>حسب الموقع ووحدة القياس</small><h2>ملخص أرصدة المستودعات</h2></div><span className="mz-stock-count">{summary.groups.length} مجموعة</span></header>
      {!summary.groups.length ? <div className="mz-stock-empty"><ChartBar size={40}/><h3>لا توجد أرصدة لإعداد التقرير</h3></div> : <div className="mz-stock-table-wrap"><table className="mz-stock-table" aria-label="أرصدة مجمعة حسب المستودع والوحدة"><thead><tr><th>المستودع</th><th>الوحدة</th><th>السجلات</th><th>الفعلية</th><th>المحجوزة</th><th>المتاحة</th><th>الموقوفة</th><th>قيمة المخزون</th><th>اكتمال التقييم</th></tr></thead><tbody>{summary.groups.map((group) => <tr key={group.key}><th scope="row">{group.warehouse_name}</th><td>{inventoryUnit(group.unit)}</td><td>{group.rows.length}</td><td>{inventoryDisplay(group.physical)}</td><td>{inventoryDisplay(group.reserved)}</td><td className="mz-stock-available">{inventoryDisplay(group.available)}</td><td>{inventoryDisplay(group.held)}</td><td>{inventoryDisplay(group.inventory_value)}</td><td><span className={"mz-stock-badge " + (group.unknownCostRows ? "is-warning" : "is-ready")}>{group.unknownCostRows ? group.unknownCostRows + " سجل بدون تقييم" : "مكتمل ضمن هذه القراءة"}</span></td></tr>)}</tbody></table></div>}
      <footer className="mz-stock-panel-footer"><span>تظهر القيمة «غير متاح» عند غياب تقييم أي سجل في المجموعة.</span></footer>
    </section>
    <div className="mz-stock-report-notes"><article className="mz-stock-surface"><h3>تجميع واضح لكل وحدة</h3><p>تُجمع القطع مع القطع، والأمتار مع الأمتار، داخل المستودع نفسه. لا يُعرض إجمالي كميات يخلط الوحدات.</p></article><article className="mz-stock-surface"><h3>التقييم من المصدر</h3><p>تكلفة الوحدة وقيمة المخزون المعتمدتان مستقلتان عن تكلفة الافتتاحية التشغيلية. لا تُحوّل القيم المجهولة إلى صفر.</p></article></div>
  </div>;
}
