const text = (value) => String(value || "").trim();

export function toggleReviewedCategory(selectedIds, categoryId) {
  const normalized = text(categoryId);
  if (!normalized) return [...(selectedIds || [])];
  const selected = new Set((selectedIds || []).map(text).filter(Boolean));
  if (selected.has(normalized)) selected.delete(normalized);
  else selected.add(normalized);
  return [...selected];
}

export function reviewedProductMatchesCategories(product, selectedIds) {
  const selected = new Set((selectedIds || []).map(text).filter(Boolean));
  if (!selected.size) return true;
  return (product?.category_ids || []).some((id) => selected.has(text(id)));
}

export function filterReviewedProducts(products, selectedIds, search) {
  const query = text(search).toLocaleLowerCase("ar");
  return (products || []).filter((product) => {
    if (!reviewedProductMatchesCategories(product, selectedIds)) return false;
    if (!query) return true;
    const optionValues = (product?.source_lines || []).flatMap((line) => {
      const options = line?.options_normalized;
      if (!options || typeof options !== "object") return [];
      return Array.isArray(options)
        ? options.flatMap((row) => [row?.name, row?.label, row?.value])
        : Object.entries(options).flatMap(([key, value]) => [key, value]);
    });
    return [
      product?.name,
      product?.sku,
      product?.product_id,
      ...(product?.source_order_numbers || []),
      ...optionValues,
    ]
      .some((value) => text(value).toLocaleLowerCase("ar").includes(query));
  });
}

export function displayReviewedQuantity(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  return Number.isInteger(number)
    ? number.toLocaleString("en-US")
    : number.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

export function selectedReviewedCategoryNames(categories, selectedIds) {
  const selected = new Set((selectedIds || []).map(text).filter(Boolean));
  return (categories || [])
    .filter((category) => selected.has(text(category?.id)))
    .map((category) => text(category?.name || category?.path || category?.id))
    .filter(Boolean);
}
