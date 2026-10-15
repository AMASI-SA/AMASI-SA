from pathlib import Path


def test_option_cost_route_contract_is_conditional_and_shared():
    source = Path("product_option_cost_routes.py").read_text(encoding="utf-8")

    assert 'RESOURCES = "mezan_cost_resources_v2"' in source
    assert 'BINDINGS = "mezan_product_option_cost_bindings_v2"' in source
    assert 'def _binding_is_selected(' in source
    assert 'if not _binding_is_selected(binding, selected_keys, selected_option_ids):' in source
    assert 'continue' in source
    assert 'option_additional += amount' in source
    assert 'additional = product_additional + option_additional' in source
    assert '"total_cost": round(base_cost + additional, 4)' in source
    assert 'impacted = await db[BINDINGS].count_documents' in source


def test_product_workspace_renders_option_and_custom_field_cost_editor():
    source = Path("../frontend/src/pages/MezanProductsWorkspace.jsx").read_text(encoding="utf-8")
    editor = Path("../frontend/src/components/products/ProductOptionCostEditor.jsx").read_text(encoding="utf-8")

    assert 'ProductOptionCostEditor' in source
    assert '<ProductOptionCostEditor productId={selectedId} options={options} customFields={customFields} />' in source
    assert 'تكلفة المنتج الأساسية تُحتسب دائمًا' in editor
    assert 'تُضاف هذه التكلفة فقط عند اختيار العميل لهذه القيمة' in editor
    assert 'تُضاف هذه التكلفة فقط عندما يرسل العميل قيمة غير فارغة' in editor
