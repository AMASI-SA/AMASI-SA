from pathlib import Path


WORKFLOW = Path(".github/workflows/ads-manager-readonly.yml")

OLD = '''              current_routes = current["routes"]
              if path == "backend/snapchat_routes.py":
                  legacy_route = (
                      "Call(func=Attribute(value=Name(id='router', "
                      "ctx=Load()), attr='post', ctx=Load()), "
                      "args=[Constant(value='/sync-all-accounts')], "
                      "keywords=[])"
                  )
                  deprecated_adapter = (
                      "Call(func=Attribute(value=Name(id='router', "
                      "ctx=Load()), attr='post', ctx=Load()), "
                      "args=[Constant(value='/sync-all-accounts')], "
                      "keywords=[keyword(arg='deprecated', "
                      "value=Constant(value=True))])"
                  )
                  current_routes = sorted(
                      legacy_route if route == deprecated_adapter else route
                      for route in current_routes
                  )
              if current_routes != baseline["routes"]:
                  errors.append(f"{path}: route declarations changed")
'''

NEW = '''              baseline_routes = baseline["routes"]
              current_routes = current["routes"]
              if path == "backend/snapchat_routes.py":
                  legacy_route = (
                      "Call(func=Attribute(value=Name(id='router', "
                      "ctx=Load()), attr='post', ctx=Load()), "
                      "args=[Constant(value='/sync-all-accounts')], "
                      "keywords=[])"
                  )
                  deprecated_adapter = (
                      "Call(func=Attribute(value=Name(id='router', "
                      "ctx=Load()), attr='post', ctx=Load()), "
                      "args=[Constant(value='/sync-all-accounts')], "
                      "keywords=[keyword(arg='deprecated', "
                      "value=Constant(value=True))])"
                  )
                  baseline_routes = sorted(
                      legacy_route if route == deprecated_adapter else route
                      for route in baseline_routes
                  )
                  current_routes = sorted(
                      legacy_route if route == deprecated_adapter else route
                      for route in current_routes
                  )
              if current_routes != baseline_routes:
                  errors.append(f"{path}: route declarations changed")
'''


def main() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    old_count = source.count(OLD)
    new_count = source.count(NEW)

    if old_count == 1 and new_count == 0:
        source = source.replace(OLD, NEW, 1)
        WORKFLOW.write_text(source, encoding="utf-8")
    elif old_count == 0 and new_count == 1:
        print("ADS_SNAP_ROUTE_GUARD_ALREADY_FIXED")
    else:
        raise SystemExit(
            f"Unexpected workflow contract: old={old_count}, new={new_count}"
        )

    verified = WORKFLOW.read_text(encoding="utf-8")
    required = (
        'baseline_routes = baseline["routes"]',
        'current_routes = current["routes"]',
        'for route in baseline_routes',
        'for route in current_routes',
        'if current_routes != baseline_routes:',
    )
    missing = [marker for marker in required if verified.count(marker) != 1]
    if missing:
        raise SystemExit(f"Symmetric guard markers missing: {missing}")
    if 'if current_routes != baseline["routes"]:' in verified:
        raise SystemExit("Stale one-sided comparison remains")

    legacy = "route:/sync-all-accounts"
    deprecated = "route:/sync-all-accounts:deprecated"

    def normalize(routes):
        return sorted(legacy if route == deprecated else route for route in routes)

    if normalize([legacy]) != normalize([deprecated]):
        raise SystemExit("Equivalent route forms did not normalize equally")
    if normalize([deprecated, "route:/new"]) == normalize([deprecated]):
        raise SystemExit("A genuine route addition was not detected")

    print("ADS_SNAP_ROUTE_GUARD_PATCHED_AND_VERIFIED")


if __name__ == "__main__":
    main()
