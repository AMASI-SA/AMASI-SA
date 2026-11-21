(async () => {
  try {
    const response = await fetch('/api/warehouse-locations/warehouses', {credentials: 'include'});
    if (!response.ok) {
      console.log('HTTP', response.status);
      return;
    }
    const data = await response.json();
    if (!Array.isArray(data.items)) {
      console.log('CHECK_FAILED: unexpected response');
      return;
    }
    console.table(data.items.map(warehouse => ({
      code: warehouse.code,
      status: warehouse.status,
      inventory_scope: warehouse.inventory_scope ?? 'غير محدد'
    })));
  } catch (error) {
    console.log('CHECK_FAILED', error.message);
  }
})();
