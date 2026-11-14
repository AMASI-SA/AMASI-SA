# Operational images in mobile assembly — 14 November 2026

## Scope and authority

The user asked to complete the server release, verify it, then publish the employee Android app. Before the app build, the user added supplier-invoice storage-full recovery and clarified that operational-product images are saved and displayed correctly in waiting review but disappear in assembly and labeling.

This task repairs only the operational-image read permission bridge. It does not change image saving, review permissions, employee grants, stock, order statuses, invoices, or shipping. Root owns GitHub integration and release operations. Separate implementer and reviewer used isolated workspaces; neither changed the shared operator workspace or production data.

## Root cause and repair

The app already carries the operational `image_url` and sends authentication through its product-image component. The server's actual image endpoint already permits assembly employees and checks merchant ownership. However, the earlier mobile request permission bridge classifies `/api/order-reviews-v1/operational-images/<id>` under the generic review prefix. An assembly-only employee is rejected with HTTP403 before reaching the image endpoint.

The production change adds a specific permission choice for **GET of one operational image ID**, with an optional trailing slash. It adds `app.page.assembly_shipping` to the existing two review permissions for that exact read only. The earlier driver rejection, enabled live grants, linked merchant resolution, and image tenant lookup remain active. Uploads, deletes, review data, and other paths retain their existing permissions.

## Reviewed source

- Initial independently tested base: `304e8793a15fd3269fd6f6d4cfba903d1760e285` (Ready release PR1912).
- Latest production base discovered before integration: `6012ceb480bf9dc0f51ec6cc64c054e5e0db3846` (PR1913), tree `2b6e4d1da108350b3d0f8637000e7de09fb3bd7b`.
- The five changes from PR1913 concern source diagnostics and its frozen release intent; they do not overlap either image file. Preserve all of them.
- Planned task branch: `codex/assembly-operational-images-20261114`.
- Runtime file: `backend/mobile_app_request_context.py`, blob `31fa05226a1e05c09f52cc11fd312da0ac2d39a6`.
- Regression file: `backend/tests/test_order_review_operational_images.py`, blob `d6f21bc7cad19d91bf5b5a4f03f4ceefec7979f3`.
- This status document is the only documentation addition. Source A retains its parent release intent. A later B may change only `release/release-intent-v5.json`.
- Exact source/intent/PR identities are recorded in the task PR after creating each immutable checkpoint; this file does not self-reference its own commit SHA.

## Verification evidence

Before the repair, an HTTP regression through the actual mobile permission bridge and image router produced200 for pending review,200 for reviewed preparation, and403 for assembly-only. This reproduced the user's reported distinction without changing the image save flow.

After the repair, the affected image and mobile request-context suites passed **45 tests, no failures/errors/skips**, in3.219s. A separate reviewer independently inspected the exact two blobs and reran all45 successfully in4.43s. Coverage includes actual image bytes/MIME, assembly read, disabled or ungranted accounts, driver denial, foreign-merchant assets, and rejection of creation/upload/update/delete/review-data requests. `git diff --check` passed. Latest-base combined qualification is recorded in the PR before freezing an intent.

```bash
cd backend
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  /workspace/scratch/864304cb148b/mezan-items-venv/bin/python \
  -m pytest tests/test_order_review_operational_images.py \
  tests/test_mobile_app_request_context.py -q --tb=short
```

Red JUnit SHA256: `54137963f87ca2a15e46932a4545c271fb66e3f31f11b7de0518a8e34c2ac97b`.

Green JUnit SHA256: `37cf17cc8c060a0a2b00fe86c3c564ca86c37138ec11612bde6edcfb51eaab97`.

The complete latest production base6012 and its relevant ancestry were reconstructed and verified in an independent checkout with no object alternates. The two baseline image files are byte-identical to the original base. Applying the same reviewed blobs preserves every one of PR1913's five changed files. Fresh combined-base verification passed45/45 in2.711s, with no failures/errors/skips and clean diff checks. Its JUnit SHA256 is `484f3745c782f3bda06a687164ec3270c086d509ec748b9df9514236de2b3cf8`.

The integration tests use supplied employee identities and `mongomock_motor`; they do not claim physical-device acceptance or a complete production JWT round trip. Live web assembly independently displayed the existing operational image with311×500 natural dimensions. No employee permission was expanded as a workaround.

## Prior Ready publication and current app work

Ready PR1912 was published once, with the new green `Deployment finished` event at14November02:49 UTC+03 and platform version `4e63025`. The unchanged official release verifier passed all3probes for deployment `304e8793a15fd3269fd6f6d4cfba903d1760e285`, source `cdf175841376d2f68605d66c1979273d23aed70d`, runtime `rg5-3e41b3f0b14d180f45cd3b242798ea348797b06e8edc770592130ac87b2a9cb0`. Verify JSON SHA256 `0a91a8097d530c0027b34bf5d6150eb536722335f98af1884af00df5b258e2c8`; stderr empty; matching lease closed and fresh official status returned `active:false`.

Read-only acceptance of the originally affected order found its current Salla status is completed while local pieces remain. The existing completed-status gate correctly keeps Ready disabled; no status or business action was changed for testing. This is not an in-progress eligibility test.

The Android release retains Ready parent `c10fcd49adaaded57b69afdf3fc73dfc8d9df3bf`, version1.0.65/code80, and integrates only the reviewed supplier receiving storage repair from donor `2fe5dfe883009b23c25b83bf09517f116fb0881a`. The combined source is `a667ad320eb1cb80b6e57e742eda58f84ec5bba4`, tree `93be9b3ef5e3464640b4fcd9fdea9ea75b715f4c`, preserved in [mobile PR352](https://github.com/AMASI-SA/amasi-mobile/pull/352). Fresh exact-source CI is pending. The old Ready-only EAS form was canceled and no new app build was submitted at this checkpoint. App source, CI, and release continuation are maintained in the mobile repository. The image defect itself requires this server change, not an additional mobile change.

## Remaining release work

This is a reviewed source checkpoint, **not a deployed image fix**. Complete fresh applicable source CI, freeze a new v5 intent-only B, verify exact-B gates and clean-clone/package rehearsal, and preserve A/B through normal merge with tree equivalence. Before any shared `/app` mutation or rehearsal, re-read official guard and require inactive state. Never alter another owner's lease or discard unrelated files. Re-read the actual production ref before merging because parallel work is active.

After successful rehearsal, use original prepare/prepublish, one normal Publish, the new green platform completion/version, and unchanged official three-probe verification followed by inactive guard. Then check image access with the authorized available identity and clearly retain any assembly-only physical-device gap. Publish the combined Android app after its fresh CI/build and server dependencies are accepted.

Production changed by this **image task** at this checkpoint: **NO**. The separately completed Ready publication is explicitly recorded above. Issue1006 is at its comment limit; use the task PR plus this document for durable continuation. No customer data, credentials, employee identifiers, or financial values belong in this record.
