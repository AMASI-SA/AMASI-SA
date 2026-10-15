from pathlib import Path

path = Path('backend/order_review_routes.py')
source = path.read_text(encoding='utf-8')
source = source.replace('\r\n', '\n')

old = '''def _can_review(user: Any) -> bool:\n    if not isinstance(user, dict):\n        return False\n    role = _normalized(user.get("role"))\n    if role == "owner" or user.get("is_owner") is True:\n        return True\n    if "orders.manage" in set(user.get("denied_permissions") or []):\n        return False\n    if role in {"admin", "operations"}:\n        return True\n    return "orders.manage" in set(user.get("extra_permissions") or [])\n'''

new = '''APP_REVIEW_PERMISSIONS = {\n    "app.role.manager",\n    "app.page.pending_review",\n    "app.page.reviewed_preparation",\n}\n\n\ndef _mobile_app_permissions(user: Any) -> set[str]:\n    if not isinstance(user, dict):\n        return set()\n    values: set[str] = set()\n    for raw in (\n        user.get("mobile_app_permissions"),\n        user.get("app_permissions"),\n    ):\n        if isinstance(raw, (list, tuple, set)):\n            values.update(_text(value) for value in raw if _text(value))\n    access = user.get("mobile_app_access")\n    if isinstance(access, dict):\n        raw = access.get("permissions")\n        if isinstance(raw, (list, tuple, set)):\n            values.update(_text(value) for value in raw if _text(value))\n    return values\n\n\ndef _can_review(user: Any) -> bool:\n    if not isinstance(user, dict):\n        return False\n    role = _normalized(user.get("role"))\n    if role == "owner" or user.get("is_owner") is True:\n        return True\n\n    # Native AMASI permissions are intentionally isolated from Mezan web\n    # permissions. A native app manager, or an employee granted either review\n    # page, may use review mutations without receiving orders.manage in Mezan.\n    mobile_permissions = _mobile_app_permissions(user)\n    if mobile_permissions.intersection(APP_REVIEW_PERMISSIONS):\n        return True\n\n    # Preserve the legacy web path for browser users and older integrations.\n    if "orders.manage" in set(user.get("denied_permissions") or []):\n        return False\n    if role in {"admin", "operations"}:\n        return True\n    return "orders.manage" in set(user.get("extra_permissions") or [])\n'''

if old not in source:
    raise SystemExit('target _can_review block not found')
source = source.replace(old, new, 1)
path.write_text(source, encoding='utf-8')
print('Order review mobile app permission bridge applied.')
