from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend" / "order_engine" / "__init__.py"

text = TARGET.read_text(encoding="utf-8")

import_anchor = "    from warehouse_reset_routes import make_warehouse_reset_router\n"
import_line = "    from mezan_attribution_owner_routes import make_mezan_attribution_owner_router\n"
if import_line not in text:
    if import_anchor not in text:
        raise SystemExit("import anchor not found")
    text = text.replace(import_anchor, import_anchor + import_line, 1)

router_anchor = "        make_warehouse_reset_router(db, current_user),\n"
router_line = "        make_mezan_attribution_owner_router(db, current_user),\n"
if router_line not in text:
    if router_anchor not in text:
        raise SystemExit("router anchor not found")
    text = text.replace(router_anchor, router_anchor + router_line, 1)

TARGET.write_text(text, encoding="utf-8")
print(f"updated {TARGET.relative_to(ROOT)}")
