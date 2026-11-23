# Inventory purchase browser acceptance

The harness imports the actual `InventoryWorkspace`, `AuthProvider`, purchase
editor, receipt queue, styles, local fonts and HTTP adapters. Only the loopback
HTTP boundary supplies synthetic fixture data. No application module is mocked,
no live tenant is accessed, and no payment endpoint is implemented by the fixture.

With the locked frontend dependencies and a sandbox-capable Chromium installed:

```bash
export INVENTORY_PURCHASE_UAT_OUTPUT=/absolute/path/outside-the-repository/inventory-purchase-browser
export PLAYWRIGHT_MODULE=/absolute/path/to/playwright
node scripts/testing/inventory_purchase/build.cjs
node scripts/testing/inventory_purchase/browser.cjs
```

`INVENTORY_PURCHASE_UAT_CHROMIUM` optionally selects an installed browser binary.
The browser uses `chromiumSandbox: true`; an unavailable sandbox fails the run.
All non-loopback network requests are blocked. The server binds an ephemeral port
on `127.0.0.1` and rejects unknown API requests.

Eight scenarios cover desktop 70% width/centering and adjacent supplier metadata,
selected product/component marks, yes/no/deferred conditional customization,
one editor with newest-first compact rows and editing in place, mobile layout,
an unpaid invoice awaiting receipt, and later receipt of exactly two of four
units. The receiving scenario verifies the remaining quantity survives a reload and
that invoice approval/receiving never call a payment or allocation endpoint.

The eighth scenario opens the actual option-role editor on desktop and mobile,
classifies a required gift-card question, its required text child and an optional
note through the real GET/PUT adapter, and checks the dependency warning and
locked physical role. After saving, those fields appear only in the neutral
customer-additions summary. Physical size is still required, draft quantity,
unit price and tax survive unchanged, and the ready stock line survives reload.
The HTTP boundary accepts only stable option IDs and the revision/fingerprint
contract: the browser sends no customer answers, cost changes or financial
commands with a policy save. Customer-facing requiredness, choice values and
conditional rules remain in the returned definition. Real database preservation
of customer answers and cost bindings is established by backend policy tests.

Output: `dist/`, `source-manifest.json`, `browser-evidence.json`, and ten PNGs:
`purchase-desktop.png`, `draft-desktop.png`, `editor-mobile.png`,
`draft-mobile.png`, `invoice-review-mobile.png`, `receipt-partial-mobile.png`,
`receipt-partial-desktop.png`, `option-roles-desktop.png`,
`option-roles-mobile.png`, `side-options-mobile.png`. Each capture asserts no
horizontal page overflow; the open role-editor captures also check every select
and the editor boundary against the viewport.
Evidence records actual source hashes, scenario results, layout measurements,
synthetic API calls, screenshots and browser/API errors. A source change after
building fails the manifest check; rebuild before rerunning.

This gate verifies actual component behavior and API request contracts. Separate
real-Mongo backend tests establish authorization, atomicity, replay, physical
quantity ceilings and financial independence; synthetic replies do not prove
those backend guarantees.
