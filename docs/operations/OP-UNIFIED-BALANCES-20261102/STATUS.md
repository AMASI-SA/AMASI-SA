# OP-UNIFIED-BALANCES — PR1700 frozen A/B candidate

Latest durable continuation: **DELIVERY-005.md** (2026-11-03), plus READY-RELEASE-20261103.md on sourceA for the complete included/deferred matrix. Main ledger **PR1687**. Candidate **PR1700**, branch release/operational-ready-r03-r05-20261103. Earlier canonical state preserved byte-for-byte in STATUS-PRE-REVIEWED-B-20261103.md (blob061fdfbfdf97d7ac53274af01997743e14b90b56); DELIVERY-REGISTER.md and records001/002 remain intact.

Owner decision6588193710: R03/R05 plus ALL safely ready fixes in one next release. Independent unfinished work is deferred, not deleted and not a reason to hold unrelated safe work. No live financial approvals or silent historical backfill.

SourceA **8cf7aa3ace5392655793fe4d15bc3c4e965362ea**, root3388d700517d3885d9ed21183aca8e7be1f92349, parentJ **9c550813afa5314dc498c20644033478aefbf80d**. It preserves source411/integration3dd5 and latest production's six additional bundle selection/provenance paths. Original integration branch is not rewritten. The old intent-edit-history classifier failure is fixed by a clean source fromJ, not guard weakening.

Intent-onlyB **c0dd5ab1762ecd99716b12d93f4d339bfdcdb8a4**, root11787597c7f79451829b3bea7e3df337aa097586. A→B changes only release/release-intent-v5.json, exact reviewed candidate blob1507e4ce97cd29a3016d5bcc4d4cb268eed7ea8f. Release candidate rg5-d8424400c8542d01be52238a8fc1deb29e7b9148cd33fadf4f1b4fd10354286b. Do NOT changeA/B, squash, rebase or append documentation to the frozen release branch. Further documentation belongs here on integration or in PR comments.

Exact sourceA CI1733 backend/335 frontend28suites passed; official artifacts independently checked. SourceA readiness41538979950 passed classification, deterministic dual builds, reproducibility, intent generation and clean checkout. All observed A workflows including CodeQL succeeded. Current B Operational41540421669 and Readiness41540421598 require fresh read; this status does not claim them green yet.

R03 legacy root-base/service receiving and R05 separate owner-reviewed native debt/payment matching are implemented/tested, together with ready supplier cutoff/SAR/states/payroll preview/delivery noncash/external-loan/warning/report work. Unsupported nested financial-only bases without unique old receiving identity, historic unallocated cash, actual provider postsettlement and full COD/profit remain explicit gaps. No payment/fee policy, statement posting, payroll missing-day or financial correction has been activated.

Visual harness built successfully from exactA by41539721823 with synthetic memory-only adapters, but local browser navigation was denied ERR_BLOCKED_BY_ADMINISTRATOR before rendering. No bypass attempted. Desktop/mobile/actual print/Excel download visual acceptance is NOT proved; unit tests are not a substitute. See DELIVERY-005.md for exact evidence and limitation.

Next: verify exact B CI and clean-clone rehearsals; complete remaining visual review in an authorized environment; read actual Emergent /app guard status before environment/release work. Never cancel another task's lease. Then controlled merge with tree=B, rehearsal/prepare/prepublish, ONE publication, live three-probe verify and active=false. PR1699 is independent and untouched. Do not infer deployment from source/CI or old PR body. Issue1006 is capped.

Production changed:NO. Financial activation:NO. /app/lease touched:NO. The frozen candidate is NOT yet merged/published.
