from pathlib import Path

path = Path("frontend/src/pages/OrderReview.jsx")
text = path.read_text(encoding="utf-8")
old = '''    const selectedIdentity = imageIdentity(visibleSelectedImage);\n    const gallery = [];\n    const seenImageIdentities = new Set();\n    for (const url of [visibleSelectedImage, ...(item.gallery || [])].filter(Boolean)) {\n        const identity = imageIdentity(url);\n        if (!identity || seenImageIdentities.has(identity)) continue;\n        seenImageIdentities.add(identity);\n        gallery.push(url);\n    }\n'''
new = '''    const selectedIdentity = imageIdentity(visibleSelectedImage);\n    const sourceGallery = (item.gallery || []).filter(Boolean);\n    const gallery = [];\n    const seenImageIdentities = new Set();\n    for (const url of sourceGallery) {\n        const identity = imageIdentity(url);\n        if (!identity || seenImageIdentities.has(identity)) continue;\n        seenImageIdentities.add(identity);\n        gallery.push(url);\n    }\n    const selectedExistsInGallery = sourceGallery.some((url) =>\n        url === visibleSelectedImage || imageIdentity(url) === selectedIdentity\n    );\n    if (item.selected_image_url && visibleSelectedImage && !selectedExistsInGallery) {\n        gallery.unshift(visibleSelectedImage);\n    }\n'''
if old not in text:
    raise SystemExit("Target gallery block not found")
path.write_text(text.replace(old, new), encoding="utf-8")

contract = Path("backend/tests/test_fulfillment_v2_contract.py")
ctext = contract.read_text(encoding="utf-8")
needle = 'assert "visibleSelectedImage" in source\n'
addition = 'assert "const sourceGallery = (item.gallery || []).filter(Boolean);" in source\nassert "if (item.selected_image_url && visibleSelectedImage && !selectedExistsInGallery)" in source\n'
if addition not in ctext:
    if needle not in ctext:
        raise SystemExit("Contract insertion point not found")
    ctext = ctext.replace(needle, needle + addition)
    contract.write_text(ctext, encoding="utf-8")

print("Default image duplicate patch applied.")
