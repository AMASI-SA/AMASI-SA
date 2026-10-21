# Courier label address

User requested district and street instead of product names. Template now combines existing address lines, district/neighborhood/block, street/street_name, building, city and country; named objects are rendered by name. Product list removed. Existing national short address and remaining amount unchanged. No backend calculation or print transport change.

Verified synthetic template values: house, district, street, city object, national short address, omitted products, duplicate removal, escaping, zero and 741.40 SAR balances. Web: four existing test-file cases executed with Node assertion adapter; mobile: node frontend/scripts/verify-courier-address.cjs passed. Physical printing and installed-device layout not verified. Full CI pending. No merge or deployment; production changed: no. Direct shipment performance change remains separate PR1456 with outstanding regression failures; do not combine by accident. Mobile excludes deadline PR289 and Chrome/biometric PR290.

Next: CI and layout verification, then governed release. Rollback: revert this isolated template commit and rebuild/deploy previous source using the applicable release process.
