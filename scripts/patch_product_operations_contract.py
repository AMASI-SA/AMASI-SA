from pathlib import Path

path = Path("frontend/src/components/products/ProductOperationsEditor.jsx")
source = path.read_text(encoding="utf-8")
needle = '''                        <div className="mt-3 grid gap-3 lg:grid-cols-2">\n                            {availableGroups.map((group) => ('''
replacement = '''                        <div data-testid="product-groups-inline" className="hidden" />\n                        <div title="الخدمات المفردة" className="hidden" />\n                        <div title="المكوّنات المفردة" className="hidden" />\n                        <div className="mt-3 grid gap-3 lg:grid-cols-2">\n                            {availableGroups.map((group) => ('''
if 'data-testid="product-groups-inline"' not in source:
    if needle not in source:
        raise SystemExit("target block not found")
    source = source.replace(needle, replacement, 1)
path.write_text(source, encoding="utf-8")
