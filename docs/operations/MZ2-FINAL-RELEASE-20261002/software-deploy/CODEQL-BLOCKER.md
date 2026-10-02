# Software deployment blocked before source promotion

Observed 2026-10-02 16:08:27 UTC. Exact B remains `e030b737ca50adb03f37b06dab2a5624d79474fa`, TREE `db5ea30adaf8aa1f5bbe37288da9366734d60ea1`. Production and rollback remain `83363097d48e034dc7140a60c290efc684e1ffde`.

The earlier complete-CI/ready claim was incomplete: all 39 Actions workflows succeeded, but the separate GitHub Advanced Security CodeQL check on the same HEAD failed. The exact commit has **66 successful + 4 skipped + 1 failed check runs**, not an all-green release Gate.

[Failed CodeQL check](https://github.com/AMASI-SA/AMASI-SA/runs/110891031629) reports **4 high-severity alerts**, titled “Clear-text logging of sensitive information”, at `backend/salla_integration/webhook_event_capture.py` lines 291, 294, 295 and 296. These are scanner findings; their validity has not yet been independently adjudicated. The entire file is byte-identical to Production base (Git blob `c78b27d0bd78b266a74d8572717e5d9aa958b17f`). That fact does not dismiss or override the failed Gate.

Owner authorization for protected source promotion and one governed software deployment is preserved. PR #1243 was briefly made ready and then restored to **OPEN / Draft** after the check was inspected. No merge API was invoked. GitHub's generated test-merge TREE matched the candidate, but that is not a completed source promotion.

No application source, Intent, Guard, assertions, branch protection, write-control or financial data changed. No prepare, prepublish, lease, merge, publish/deploy or automatic retry occurred. Deployment attempts remain **0**.

Next safe step: report and stop; resolve/validate the CodeQL findings before considering source promotion. Do not dismiss findings just to obtain green CI. If a source fix is required, it needs a new reviewed source/Intent and fresh gates; do not silently replace immutable B.

Full Business UAT stays NOT PASS. Production financial writes by this work = **0**; Opening, Inventory Initialization, Activation/P08, Backfill and financial schedules remain prohibited.

[Raw audit](PRE-PROMOTION-GATE-AUDIT.json)
