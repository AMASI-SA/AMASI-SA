from pathlib import Path

registry_path = Path("backend/preparation_file_registry.py")
registry = registry_path.read_text(encoding="utf-8")

old_imports = '''from order_review_export_controls import (\n    assignable_employee_view,\n    user_can_manage_preparation,\n)\nfrom ai_store_access_contract import effective_permissions, find_role_assignments\n'''
new_imports = '''from mobile_app_permissions import (\n    MOBILE_APP_ACCESS,\n    MOBILE_APP_ACCESS_OWNER_FIELD,\n    effective_mobile_app_permissions,\n)\nfrom order_review_export_controls import assignable_employee_view\n'''
if old_imports not in registry:
    raise SystemExit("employee selector import block not found")
registry = registry.replace(old_imports, new_imports, 1)

start = registry.index("async def _assignable_employees(")
end = registry.index("\n\nasync def _next_file_sequence", start)
replacement = '''async def _assignable_employees(\n    db: Any,\n    *,\n    user_id: str,\n    reviewer: dict[str, Any],\n) -> list[dict[str, Any]]:\n    \"\"\"Return active AMASI-app employees eligible for preparation assignment.\n\n    Eligibility comes only from the native-app access contract, never Mezan web\n    RBAC. The assignee must have active app access to ``app.page.my_products``\n    because that is the page used to work assigned preparation files. The\n    merchant owner remains eligible through the native owner override. Employees\n    that already owned preparation files are surfaced first.\n    \"\"\"\n    access_rows = await db[MOBILE_APP_ACCESS].find(\n        {\n            MOBILE_APP_ACCESS_OWNER_FIELD: user_id,\n            \"enabled\": {\"$ne\": False},\n        },\n        {\"_id\": 0, \"user_id\": 1, \"permissions\": 1, \"enabled\": 1},\n    ).to_list(1000)\n    access_by_id = {\n        _text(row.get(\"user_id\")): row\n        for row in access_rows\n        if _text(row.get(\"user_id\"))\n    }\n\n    owner_id = _text(reviewer.get(\"id\"))\n    candidate_ids = sorted(set(access_by_id) | ({owner_id} if owner_id else set()))\n    docs = await db.users.find(\n        {\"id\": {\"$in\": candidate_ids}},\n        {\n            \"_id\": 0,\n            \"id\": 1,\n            \"name\": 1,\n            \"email\": 1,\n            \"role\": 1,\n            \"disabled\": 1,\n            \"is_active\": 1,\n            \"deleted_at\": 1,\n        },\n    ).to_list(max(len(candidate_ids), 1))\n    users_by_id = {_text(row.get(\"id\")): row for row in docs if _text(row.get(\"id\"))}\n    if owner_id and owner_id not in users_by_id:\n        users_by_id[owner_id] = reviewer\n\n    eligible_by_id: dict[str, dict[str, Any]] = {}\n    for employee_id in candidate_ids:\n        row = users_by_id.get(employee_id)\n        if not row:\n            continue\n        if (\n            row.get(\"disabled\") is True\n            or row.get(\"is_active\") is False\n            or row.get(\"deleted_at\")\n            or not _text(row.get(\"email\"))\n        ):\n            continue\n\n        is_owner = employee_id == owner_id and (\n            _text(row.get(\"role\")).casefold() == \"owner\"\n            or row.get(\"is_owner\") is True\n            or reviewer.get(\"is_owner\") is True\n        )\n        permissions = set(\n            effective_mobile_app_permissions(\n                access_by_id.get(employee_id),\n                account_active=True,\n            )\n        )\n        if not is_owner and \"app.page.my_products\" not in permissions:\n            continue\n        eligible_by_id[employee_id] = row\n\n    employee_ids = list(eligible_by_id)\n    previously_assigned: set[str] = set()\n    if employee_ids:\n        rows = await db[REGISTRY].find(\n            {\n                \"user_id\": user_id,\n                \"responsible_employee_id\": {\"$in\": employee_ids},\n            },\n            {\"_id\": 0, \"responsible_employee_id\": 1},\n        ).to_list(5000)\n        previously_assigned = {\n            _text(row.get(\"responsible_employee_id\"))\n            for row in rows\n            if _text(row.get(\"responsible_employee_id\"))\n        }\n\n    result = [assignable_employee_view(row) for row in eligible_by_id.values()]\n    result.sort(\n        key=lambda row: (\n            0 if row[\"id\"] in previously_assigned else 1,\n            _text(row.get(\"name\")).casefold(),\n            row[\"id\"],\n        )\n    )\n    return result\n'''
registry = registry[:start] + replacement + registry[end:]
registry = registry.replace(
    '"message": "اختر موظفًا مسؤولًا نشطًا يملك صلاحية إدارة التجهيز.",',
    '"message": "اختر موظفًا نشطًا لديه وصول لتطبيق أماسي وصفحة إدارة منتجاتي.",',
    1,
)
registry_path.write_text(registry, encoding="utf-8")

context_path = Path("backend/mobile_app_request_context.py")
context = context_path.read_text(encoding="utf-8")
old_route = '("/api/preparation-file-registry-v1", _permissions("app.page.my_products")),'
new_route = '("/api/preparation-file-registry-v1", _permissions("app.page.reviewed_preparation")),'
if old_route not in context:
    raise SystemExit("preparation registry mobile route mapping not found")
context = context.replace(old_route, new_route, 1)
context_path.write_text(context, encoding="utf-8")

print("Preparation app employee selector and registry permission alignment applied.")
