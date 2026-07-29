from pathlib import Path

frontend_path = Path("frontend/src/pages/OrderReview.jsx")
contract_path = Path("backend/tests/test_fulfillment_v2_contract.py")

frontend = frontend_path.read_text(encoding="utf-8")
old_frontend = '''    const sourceGallery = (item.gallery || []).filter(Boolean);\n    const gallery = [];\n    const seenImageIdentities = new Set();\n    for (const url of sourceGallery) {\n        const identity = imageIdentity(url);\n        if (!identity || seenImageIdentities.has(identity)) continue;\n        seenImageIdentities.add(identity);\n        gallery.push(url);\n    }\n    const selectedExistsInGallery = sourceGallery.some((url) =>\n        url === visibleSelectedImage || imageIdentity(url) === selectedIdentity\n    );\n    if (item.selected_image_url && visibleSelectedImage && !selectedExistsInGallery) {\n        gallery.unshift(visibleSelectedImage);\n    }\n'''
new_frontend = '''    const sourceGallery = (item.gallery || []).filter(Boolean);\n    const gallery = [];\n    const seenImageIdentities = new Set();\n    for (const url of sourceGallery) {\n        const identity = imageIdentity(url);\n        if (!identity || identity === selectedIdentity || seenImageIdentities.has(identity)) continue;\n        seenImageIdentities.add(identity);\n        gallery.push(url);\n    }\n'''
if old_frontend not in frontend:
    raise SystemExit("Frontend insertion point not found")
frontend_path.write_text(frontend.replace(old_frontend, new_frontend), encoding="utf-8")

contract = contract_path.read_text(encoding="utf-8")
old_contract = '''    assert 'const sourceGallery = (item.gallery || []).filter(Boolean);' in frontend_source\n    assert 'const selectedExistsInGallery = sourceGallery.some((url) =>' in frontend_source\n    assert 'gallery.unshift(visibleSelectedImage);' in frontend_source\n'''
new_contract = '''    assert 'const sourceGallery = (item.gallery || []).filter(Boolean);' in frontend_source\n    assert 'identity === selectedIdentity' in frontend_source\n    assert 'gallery.unshift(visibleSelectedImage);' not in frontend_source\n'''
if old_contract not in contract:
    raise SystemExit("Contract insertion point not found")
contract_path.write_text(contract.replace(old_contract, new_contract), encoding="utf-8")

print("Main image thumbnail duplicate patch applied.")
