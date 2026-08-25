from pathlib import Path

path = Path("backend/reviewed_preparation_batches.py")
text = path.read_text(encoding="utf-8")
old = '''def _line_from_batch_storage(\n    row: dict[str, Any],\n    batch: dict[str, Any] | None = None,\n) -> ProductLine:\n    image_bytes = None\n'''
new = '''def _line_from_batch_storage(\n    row: dict[str, Any],\n    batch: dict[str, Any] | None = None,\n) -> ProductLine:\n    # Rebuild customer-selected option fields from the immutable reviewed\n    # file snapshot when older/stale batch rows are missing the projected\n    # convenience fields. Both the employee preparation PDF and the supplier\n    # dispatch PDF pass through this function, so one canonical fallback keeps\n    # both files consistent without changing the original Salla values.\n    stored_spec_fields = [\n        field for field in (row.get("file_spec_fields") or [])\n        if isinstance(field, dict)\n    ]\n    fallback_fields = _card_field_projection(stored_spec_fields, row.get("preparation_note"))\n    image_bytes = None\n'''
if old not in text:
    raise SystemExit("target function header not found")
text = text.replace(old, new, 1)
old2 = '''        customer_name=_text(row.get("customer_name")) or None,\n        note=_text(row.get("note")) or None,\n'''
new2 = '''        customer_name=(\n            _text(row.get("customer_name"))\n            or _text(fallback_fields.get("customer_name"))\n            or None\n        ),\n        note=(\n            _text(row.get("note"))\n            or _text(fallback_fields.get("note"))\n            or None\n        ),\n'''
if old2 not in text:
    raise SystemExit("customer/note projection target not found")
text = text.replace(old2, new2, 1)
old3 = '''        size=_text(row.get("size")) or None,\n        color=_text(row.get("color")) or None,\n        product_id=_text(row.get("product_id")) or None,\n        sku=_text(row.get("sku")) or None,\n        product_options=dict(row.get("product_options") or {}),\n'''
new3 = '''        size=(\n            _text(row.get("size"))\n            or _text(fallback_fields.get("size"))\n            or None\n        ),\n        color=(\n            _text(row.get("color"))\n            or _text(fallback_fields.get("color"))\n            or None\n        ),\n        product_id=_text(row.get("product_id")) or None,\n        sku=_text(row.get("sku")) or None,\n        product_options=(\n            dict(row.get("product_options") or {})\n            or dict(fallback_fields.get("product_options") or {})\n        ),\n'''
if old3 not in text:
    raise SystemExit("size/color/options projection target not found")
text = text.replace(old3, new3, 1)
path.write_text(text, encoding="utf-8")
print("Preparation customer options PDF fallback applied.")
