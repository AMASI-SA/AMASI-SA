# Build44 / Samsung printing acceptance plan

Status: source review only. No APK installation, device interaction, printer job,
provider call, Android change, deployment, or physical acceptance was performed.

## Exact candidate

- Android PR #269 source: `5680e7b704d98643dde22fd8164d3bd89ed2db7c` in
  `AMASI-SA/amasi-mobile`, inspected with `git show` from the local repository.
- `frontend/app.json`: package `com.amasi.sa.mobile`, version `1.0.29`, Android
  versionCode `44`. This identifies source intent, not an installed APK.
- Relevant source paths have no diff between `9f6d02206333ca43ef2b2019214cb1523a8cc496`
  and this exact PR #269 commit: `frontend/src/features/assemblyShipping/`,
  `frontend/src/features/myProducts/supplierDispatchPrint.ts`, and
  `frontend/src/features/orderReview/repository.ts`.
- Before device acceptance, match the reviewed build evidence, APK SHA-256,
  signing certificate, package/versionCode, source commit and installed package.
  Use the candidate's existing `tools/release/build44_release.py` gates and
  `docs/FINAL_CI_BUILD44_GATE.md`; this plan does not authorize a new build,
  publish, or replacement APK.

## Existing print contract

`AssemblyShippingStage.tsx:727` obtains a fresh label through the existing
carrier-label refresh route. Its request/search ownership and shipment identity
checks run before opening the local document (`:733–741`).

`carrierLabelPrint.ts:234` downloads `label_url` with an Accept header and no API
Authorization header. It requires a successful HTTP result and saves PDF bytes
in the application cache. The server capability route must therefore preserve
this unauthenticated, short-lived bearer download contract. Backend rejection
must return an error status, never an HTML error page with HTTP 200 (the existing
client can convert successful HTML responses to PDF).

`supplierDispatchPrint.ts:112–127` converts the local PDF to a content URI and
opens Android ACTION_VIEW with MIME `application/pdf` and read permission. A
share-sheet fallback exists. Opening a viewer is not proof of a printer job.

## Controlled acceptance sequence

Use synthetic/noncustomer labels and an isolated backend/provider fixture first.
Any later real-order or printer action needs the task's applicable authorization.

1. Record Samsung device model, Android/One UI version, installed package/build
   identity, PDF viewer, print-service name/version, printer model, connection,
   and intended media size. Do not assume Samsung Print Service is installed.
2. With completed assembly and a current provider-completed order, verify an
   existing created SMSA label and an existing created iMile label. Confirm the
   downloaded bytes match `document_sha256`; record no bearer URL in evidence.
3. Open the actual device PDF viewer, select the intended printer/service, and
   print one controlled sample per carrier. Check page count, media dimensions,
   orientation, margins, absence of clipping/scaling damage, Arabic legibility,
   and scan the printed barcode to confirm the exact expected AWB. Record the
   completed printer output separately from viewer-open success.
4. Exercise the store-courier QR path separately: correct order identity and
   assignment, fresh document, readable QR, and no external AWB creation.
5. During download/revalidation, simulate shipment/carrier change and terminal
   order status. Confirm rejection and no opening of an older cached document.
   Repeat for wrong AWB, HTML/error bytes, encrypted/malformed PDF, expired or
   wrong-order capability, interrupted download, and printer/viewer absence.
6. Check duplicate taps, back/navigation during fetch, retry after a rejected
   attempt, and network recovery. Confirm a successful fresh retry works while
   a stale response cannot open or overwrite the newer order's label.
7. Capture only sanitized evidence: request counts/methods, status/reason codes,
   source/build identity, expected document hash, and controlled printed sample
   results. No bearer token, signed source URL, customer PDF, or credentials.

## PR #1330 remains a separate compatibility decision

Compared range: backend base `b41cd9cc36a738a0528dd89ddc7349b31db2a361` through
`4616ed11557c85daee0d49613c4fd6e654720f70`. Its displayed-review approval requires
an `approval_token`. Exact PR #269 source `repository.ts:715` still sends only
`expected_revision`; therefore new review approval would reject with
`review_approval_required`. Its companion mobile approval change is separate
from PR #269. Do not silently weaken the backend token guard or claim compatible
rollout. Shipping print compatibility does not establish review compatibility.

The integrated #1327/#1331 runtime review files remain unchanged from that base.
Direct overlapping fixtures are `test_g47_component_lifecycle_integration.py`
and `test_review_local_assembly.py`: preserve positive canonical in-progress
evidence alongside any future approval-token fixtures. PR #1330 was not applied
by this review; textual patch-conflict acceptance is not claimed.

## Capability deployment limits

Application log filtering and structured sanitization cannot prove sanitization
inside a hosting proxy, CDN, APM SDK, external error collector, device history,
or an external viewer. The repository's safe nginx timing format is an example,
not proof of deployed settings. Verify those layers independently before treating
capability URLs as absent from logs. Python logging should be configured before
installing filters; reinstall after replacing its record factory or handlers.

The immutable-document collection hashes token lookup keys, but workflow label
URLs still contain the usable capability. Do not describe storage as exclusively
hashed tokens. A capability remains a reusable bearer secret until expiry.

The inherited phase-one parser was in-process: its 2 MiB/eight-page limits alone
did not enforce a hard CPU/memory execution bound. Evaluate the separately owned
parser-isolation changes and exact runtime resource-limit evidence independently;
this source/device plan does not certify them. Device/Samsung acceptance and
deployed proxy/APM redaction remain unverified.
