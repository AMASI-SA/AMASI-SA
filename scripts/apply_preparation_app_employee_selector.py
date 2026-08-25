from pathlib import Path

path = Path("backend/preparation_file_registry.py")
text = path.read_text(encoding="utf-8")

old_imports = '''from order_review_export_controls import (\n    assignable_employee_view,\n    user_can_manage_preparation,\n)\nfrom ai_store_access_contract import effective_permissions, find_role_assignments\n'''
new_imports = '''from order_review_export_controls import assignable_employee_view\n'''
if old_imports not in text:
    raise SystemExit("employee selector import block not found")
text = text.replace(old_imports, new_imports, 1)

start = text.index("async def _assignable_employees(")
end = text.index("\n\nasync def _next_file_sequence", start)
replacement = '''async def _assignable_employees(\n    db: Any,\n    *,\n    user_id: str,\n    reviewer: dict[str, Any],\n) -> list[dict[str, Any]]:\n    \"\"\"Return active app-login employees eligible for preparation assignment.\n\n    Preparation assignment is a mobile-app workflow. Do not hide a valid app\n    employee merely because their Mezan/AI-store preparation permissions differ.\n    The employee must still be an active, non-deleted user account with an email\n    login. Employees that already owned preparation files are surfaced first.\n    \"\"\"\n    docs = await db.users.find(\n        {\"created_by\": user_id},\n        {\n            \"_id\": 0,\n            \"id\": 1,\n            \"name\": 1,\n            \"email\": 1,\n            \"role\": 1,\n            \"disabled\": 1,\n            \"is_active\": 1,\n            \"deleted_at\": 1,\n        },\n    ).sort(\"name\", 1).to_list(500)\n\n    candidates = [reviewer, *docs]\n    active_by_id: dict[str, dict[str, Any]] = {}\n    for row in candidates:\n        employee_id = _text(row.get(\"id\"))\n        email = _text(row.get(\"email\"))\n        if (\n            not employee_id\n            or employee_id in active_by_id\n            or not email\n            or row.get(\"disabled\") is True\n            or row.get(\"is_active\") is False\n            or row.get(\"deleted_at\")\n        ):\n            continue\n        active_by_id[employee_id] = row\n\n    employee_ids = list(active_by_id)\n    previously_assigned: set[str] = set()\n    if employee_ids:\n        rows = await db[REGISTRY].find(\n            {\n                \"user_id\": user_id,\n                \"responsible_employee_id\": {\"$in\": employee_ids},\n            },\n            {\"_id\": 0, \"responsible_employee_id\": 1},\n        ).to_list(5000)\n        previously_assigned = {\n            _text(row.get(\"responsible_employee_id\"))\n            for row in rows\n            if _text(row.get(\"responsible_employee_id\"))\n        }\n\n    result = [assignable_employee_view(row) for row in active_by_id.values()]\n    result.sort(\n        key=lambda row: (\n            0 if row[\"id\"] in previously_assigned else 1,\n            _text(row.get(\"name\")).casefold(),\n            row[\"id\"],\n        )\n    )\n    return result\n'''
text = text[:start] + replacement + text[end:]

text = text.replace(
    '"message": "اختر موظفًا مسؤولًا نشطًا يملك صلاحية إدارة التجهيز.",',
    '"message": "اختر موظفًا نشطًا لديه حساب دخول للتطبيق.",',
    1,
)

path.write_text(text, encoding="utf-8")
print("Preparation app employee selector applied.")
