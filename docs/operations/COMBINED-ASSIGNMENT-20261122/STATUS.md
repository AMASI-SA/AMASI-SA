# Combined employee and supplier assignment — 2026-11-22

IMPLEMENTATION_COMPLETE: true
MOBILE_IMPLEMENTATION_COMPLETE: true
BACKEND_IMPLEMENTATION_COMPLETE: true
READY_FOR_RELEASE: false
APK_PUBLISHED: false
BACKEND_DEPLOYED: false

## Delivered user contract

The existing employee-assignment form now supports assigning a preparation employee and a supplier in one save. The user's last refinement is binding:

| Choice in the supplier field | Required input | Result for the chosen employee |
| --- | --- | --- |
| An actual supplier | A currently valid supplier | The same physical pieces appear in **قيد التنفيذ**, in that supplier's account, on the first scoped read after success. |
| **ترك إسناد المورد لموظف التجهيز** | Explicitly choose this option | Employee-only assignment; the pieces remain **بانتظار الإسناد للمورد** for the employee to choose the supplier. |
| Empty | Save is blocked | Neither implicit deferral nor an incomplete combined assignment is submitted. |

The supplier field starts empty. The actual assignment operator remains the audit actor; the chosen preparation employee owns the work. This does not add polling or cross-device push: the existing screen-open/focus refresh reads the committed assignment.

The supplier PDF mentioned during review is the existing file under **إدارة منتجاتي → قيد التنفيذ → اليوم → المورد → ملفات المورد**. Pressing its file number opens the PDF. This addition does not introduce another supplier page.

## Implementation and recovery

The new versioned backend contract is:

- `GET /preparation-assignment-v2/options`: authenticated actor/scope, supported modes and active suppliers.
- `POST /preparation-assignment-v2/requests`: immutable employee, supplier-or-defer mode, exact physical selections/revisions, file metadata and optional required deadline.
- `GET /preparation-assignment-v2/requests/{client_request_id}`: read-only ABSENT/PENDING/COMMITTED proof for the original actor and command.

Preparation records only the request identity/lease before preparing the existing PDF outside the publication transaction. Publication rechecks live employee authority, assignee access, supplier/category/service rules, current order source/revisions and operational instructions. The registry, batch, physical allocations, pieces, supplier dispatch when selected, audit events and immutable receipt commit together under the narrowly scoped operational owner transaction.

A combined success requires the dispatch and all physical proofs. Failed publication rolls back all those effects. Unknown HTTP/commit results retain the original durable mobile attempt and are resolved with GET or an explicit resume using the same request identity. Repeated taps, process restart and lost acknowledgements do not create another file or dispatch. A changed employee, supplier, mode, title or physical selection cannot reuse the same command identity. A late failed preparer cannot claim not-applied after another attempt commits.

Legacy saved employee-only attempts keep their original contract. Only explicit options HTTP404/501 offers the existing employee-only path, and the user must still choose deferral. Supplier assignment never silently falls back to employee-only. Permission/network/invalid-contract failures stay errors.

The client adds one exact POST under the existing order-review write flag. Authentication/transport bytes and historical preservation pins remain unchanged; the normalizers admit only the exact anchored addition and reject missing, duplicated, moved or broadened gates.

## Ownership, physical data and preserved behavior

- The legacy dispatch custody identity remains the selected employee. Separate performed-by fields and piece/events preserve the actual operator.
- The new supplier-account reader uses the piece's current responsible employee, so an assignment by a different operator produces actual product rows, not just counts.
- Supplier PDF and remaining-PDF use the true native actor. An unrelated employee with only MyProducts is denied. The actual assignee, true owner, explicit native `app.role.manager`, and genuine legacy web manager retain their appropriate PDF access.
- Supplier share status/evidence/confirmation use the true actor and keep their existing owner-only-for-that-file policy. No external message was sent during this work.
- The existing 50 physical-piece maximum, quantity-one selections, revision/anchor checks, bundle-parent/direct-stock exclusions, category/service compatibility, customer specification snapshot and full-order allocation coverage remain enforced.
- Assignment does not complete supplier service obligations or invent supplier receipts, inventory movements or financial entries. Provider/order-stage reconciliation stays after the local transaction and preserves full-order coverage rules.
- No scanner, dependency, native module, package identifier, Android version or OTA-policy change was made. All earlier Stage6, notifications, refunds, balance/tracking and provider-statement work remains in the candidate.

## Final verification

All counts below are executed checks on the frozen source. Interrupted/resource-constrained attempts are not counted as passes.

| Gate | Result |
| --- | --- |
| Actual mobile screen/repository/durable journal | 31/31 PASS |
| Same mobile verifier with independent real HTTP response replay | 36/36 PASS (the original31 plus5 actual-contract cases) |
| Backend assignment plus adjacent supplier/preparation regressions in one process | 268/268 PASS; zero skips/errors/failures; 59.40s |
| Existing native authority and operational boundary gates, separate startup context | 43/43 PASS plus35 subtests; zero skips/errors/failures; 13.19s |
| Full TypeScript | PASS |
| Targeted ESLint | 0 errors; 2 pre-existing hook-dependency warnings |
| Existing preparation-file recovery | PASS |
| Build25 authentication preservation | PASS; immutable pins retained |
| Source-addition review | 25/25 PASS, including exact assignment-gate anti-broadening cases |
| Workflow YAML and git diff checks | PASS |

The 268-case backend run contains26 writer cases,44 independent HTTP/transaction cases,31 supplier-dispatch cases,63 current-status waiting cases,58 piece-operation cases,6 progress cases,6 remaining-PDF cases,2 line-service cases,30 category cases and2 share-evidence cases. The independent checks use a shared canonical synthetic seed but an independent HTTP app/assertions. Planning, permission bridges, Mongo transactions, materialization, readers and PDF generation are real; only the external provider boundary is replaced.

Local transaction tests used isolated loopback **Mongo7.0.16 replica sets**, with writable-primary verification and normal exit0 for tests and database shutdown on both final runs. CI is configured for its existing **Mongo8.0.12** service. No production database or external provider write was performed.

Initial combined collection exposed native guards installed by test imports before cleanup. The two new test fixtures now install the real guards during each HTTP test and restore the affected globals afterwards; legacy assertions and product source were not weakened. The final268-case run proves isolation in one process. Existing native-context/operational tests retain their distinct startup context in the separate43-case run. Parallel database runs encountered resource pressure; the final recorded runs are sequential and retain the original timeouts.

Independent review closed three defects before the final gate: cross-employee supplier product visibility, late preparation failure after lease takeover, and native PDF/share identity handling. No confirmed review defect remains in this task's scope.

## Fresh Android86 export

Profile `mezan-web-assembly-pilot`: **PASS**, 16.97s.

- Version1.0.71, Android versionCode86, package `com.amasi.sa.mobile`.
- 1,835 modules,44 assets,6,901,159-byte Hermes bundle.
- API `https://mezansalla.com/api`; OTA disabled, `checkAutomatically=NEVER`.
- Bundle SHA-256: `9efc204095a86b02b3483e9857ffe1a8a9053eb3339474fef6647cda6803a7d5`.
- All320 runtime/config source inputs were unchanged before/after export and were rechecked against the saved mobile source. Aggregate SHA-256: `8de103feb1ab51d8592dc5aafb7cf34702af5ad1fa6187425c34948dbaff7e5f`.

This is a JS/assets export, not an APK, EAS build, publication or physical-device UAT. No device-speed claim is inferred from these tests.

## Sources and release handoff

- Mobile: AMASI-SA/amasi-mobile, PR373, branch `codex/assembly-queues-fast-read-20261121`, source commit `4e748d3b893102df9efa7804ee24e7b1b6720192`; parent `ed44e0a7c940bfc58bc54faa53009a43db32160c`.
- Backend: AMASI-SA/AMASI-SA, PR2050, same task branch, source commit `e0476d2a16d386f6c91aa036175fee819b952cf9`; parent `cfbde46a923711b513ba37c306b5e53a4717c884`.
- Previous Stage6 source is preserved: mobile `b115caf662023b68c5889333acbcfc76f545fdf5`, backend `5278f6958385937d218afe4735679152b8f433d7`.

Fresh exact-mobile-source CI evidence: MobileCI run45065905428/job137341856676 and combined Android run45065905349/job137341856190 both returned failure with runner_id0 and an empty steps array for source `4e748d3b893102df9efa7804ee24e7b1b6720192`. No workflow step executed; no billing/quota diagnosis is inferred. The exact backend source `e0476d2a16d386f6c91aa036175fee819b952cf9` had no PR-triggered workflow runs in the fresh lookup. PR2050 remained draft/open and nonmergeable against its existing base; that base is not asserted to be current production.

App-first publication remains authorized once its release gates are satisfied. Backend integration/deployment must preserve the repository's v5 procedure and the other conversation's release lease. No release lease, production state or third-party message was changed here. The existing hourly automation `Automation_7fd7e2fc16f8819199903b78592d5757` remains the continuation mechanism; update its handoff to these completed sources without changing its schedule or creating a duplicate task.

Before release, fetch the actual branch heads, required CI, current production source and release guard state. Continue from this verified checkpoint; do not repeat implementation or publish the preceding candidate that lacks combined assignment.
