from pathlib import Path

path = Path("backend/product_v2_workspace_routes.py")
source = path.read_text(encoding="utf-8")

old = '''    products = await db[PRODUCTS].find(
        {"user_id": user_id, "archived": {"$ne": True}},
'''
new = '''    products = await db[PRODUCTS].find(
        {
            "user_id": user_id,
            "archived": {"$ne": True},
            "cost_setup_complete": {"$ne": True},
        },
'''
count = source.count(old)
if count != 1:
    raise SystemExit(f"sold product query anchor expected once, found {count}")
source = source.replace(old, new, 1)

old_requested = '''            "user_id": user_id,
            "archived": {"$ne": True},
            "$or": [
'''
new_requested = '''            "user_id": user_id,
            "archived": {"$ne": True},
            "cost_setup_complete": {"$ne": True},
            "$or": [
'''
count = source.count(old_requested)
if count != 1:
    raise SystemExit(f"requested cohort query anchor expected once, found {count}")
source = source.replace(old_requested, new_requested, 1)

path.write_text(source, encoding="utf-8")
