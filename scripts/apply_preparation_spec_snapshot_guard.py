from pathlib import Path

path = Path("backend/reviewed_preparation_batches.py")
text = path.read_text(encoding="utf-8")

old_import = '''from order_review_spec_replacements import (\n    canonical_spec_key,\n    supplier_file_spec_fields,\n)\n'''
new_import = '''from order_review_spec_replacements import (\n    canonical_spec_key,\n    extract_item_specs,\n    supplier_file_spec_fields,\n)\n'''
if old_import not in text:
    raise SystemExit("spec replacement import block not found")
text = text.replace(old_import, new_import, 1)

anchor = '''            spec_fields = supplier_file_spec_fields(identity, state)\n            card_fields = _card_field_projection(\n                spec_fields,\n                state.get("preparation_note"),\n            )\n'''
replacement = '''            spec_fields = supplier_file_spec_fields(identity, state)\n            # Fail closed before materialising the employee preparation file:\n            # every customer option visible in Waiting Review must be frozen\n            # into the immutable file snapshot unless the reviewer explicitly\n            # hid that spec from exported files. The supplier PDF later reuses\n            # this exact snapshot, so this one guard protects both files.\n            hidden_spec_keys = {\n                canonical_spec_key(value)\n                for value in state.get("supplier_export_excluded_spec_keys", []) or []\n                if canonical_spec_key(value)\n            }\n            required_specs = {\n                row["spec_key"]: row\n                for row in extract_item_specs(identity)\n                if row.get("spec_key") and row["spec_key"] not in hidden_spec_keys\n            }\n            snapshotted_specs = {\n                canonical_spec_key(row.get("spec_key") or row.get("name"))\n                for row in spec_fields\n                if isinstance(row, dict)\n                and canonical_spec_key(row.get("spec_key") or row.get("name"))\n                and _text(row.get("value"))\n            }\n            missing_spec_keys = sorted(set(required_specs) - snapshotted_specs)\n            if missing_spec_keys:\n                missing_labels = [required_specs[key]["name"] for key in missing_spec_keys]\n                raise HTTPException(\n                    status_code=409,\n                    detail={\n                        "code": "preparation_customer_options_snapshot_incomplete",\n                        "message": (\n                            "تعذّر إنشاء ملف التجهيز لأن بعض خيارات العميل لم تُحفظ بالكامل: "\n                            + "، ".join(missing_labels)\n                            + ". حدّث الطلب ثم أعد المحاولة."\n                        ),\n                        "order_number": order_number,\n                        "order_item_id": order_item_id,\n                        "missing_spec_keys": missing_spec_keys,\n                    },\n                )\n            card_fields = _card_field_projection(\n                spec_fields,\n                state.get("preparation_note"),\n            )\n'''
if anchor not in text:
    raise SystemExit("spec snapshot anchor not found")
text = text.replace(anchor, replacement, 1)

path.write_text(text, encoding="utf-8")
print("Preparation customer option snapshot guard applied.")
