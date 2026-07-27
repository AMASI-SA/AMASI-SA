from pathlib import Path


def test_option_cost_route_contract_is_conditional_and_shared():
    source = Path("product_option_cost_routes.py").read_text(encoding="utf-8")

    assert 'RESOURCES = "mezan_cost_resources_v2"' in source
    assert 'BINDINGS = "mezan_product_option_cost_bindings_v2"' in source
    assert 'if key not in selected_keys:' in source
    assert 'continue' in source
    assert 'additional += amount' in source
    assert '"total_cost": round(base_cost + additional, 4)' in source
    assert 'impacted = await db[BINDINGS].count_documents' in source


def test_product_workspace_renders_option_cost_editor():
    source = Path("../frontend/src/pages/MezanProductsWorkspace.jsx").read_text(encoding="utf-8")
    editor = Path("../frontend/src/components/products/ProductOptionCostEditor.jsx").read_text(encoding="utf-8")

    assert 'ProductOptionCostEditor' in source
    assert '<ProductOptionCostEditor productId={selectedId} options={options} />' in source
    assert 'تكلفة المنتج الأساسية تُحتسب دائمًا' in editor
    assert 'تُضاف هذه التكلفة فقط عند اختيار العميل لهذه القيمة' in editor
