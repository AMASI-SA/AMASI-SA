# Preview refresh complete

Verified on 2026-10-27 Asia/Riyadh. This is Preview acceptance, not a new Production publication.

- Source: `d1976b99e35c684843c7a6e69d365dd2af0eb2e8`.
- Production reference retained: `d3988c0be954daecdc2335c8699d20926e1bd4a3`.
- CI: https://github.com/AMASI-SA/AMASI-SA/actions/runs/40399029362 (success).
- ZIP artifact 12688176545: `a7cc88d5a524df89973165d7bae53bdbc982118a7d392b0de91b060c8dca6f17`.
- All 4702 source blobs and 4722 manifest entries verified before staging.
- Runtime: `/root/mezan-preview-supplier-r2-20261026`.
- Adapter SHA256: `261b7c375f93045c4412cd7415b1c430bf89214f756688e4e5cb98678c23dc13`.
- Launcher SHA256: `79b933002649acb48c2ebf13b15f244bc47c688caa2921d7b8db9d26e6ae72d6`.
- Supervisor SHA256: `768396d0f183c1d942b27aacc783828bf875d779d1e129aa85606977eedc6e20`.

The construction rehearsal passed with all network connections and child processes forbidden, without starting lifespan. The exact seven original startup handlers were recognized and replaced by `isolated_startup`; no automatic worker was enabled. Preview intentionally contains no Production release identity; the corresponding missing-identity log is expected and is not represented as Production verification.

The guarded switch changed exactly backend/frontend command and directory. Backend PID 103545 and frontend PID 103671 both referenced the new runtime. Three local health reads returned `ok:true`. Port 3000 returned Preview metadata with the exact source, Preview API origin, disabled workers and denied external network. Index SHA256 remained `bc0d3347bd7acc7b3e509ba28c33f36ea56b8fab3c8a307b3a0fe8ad0b3c6ee5`. Key and training env bytes matched the old runtime exactly. Final release guard returned `active:false`. Remote summary: `VERIFIED.json` under the new runtime.

Browser acceptance at https://salla-analytics.preview.emergentagent.com/suppliers-v2:

- Existing session opened the supplier page without logging in again.
- Existing supplier remained visible with the message that its previous service linkage was preserved until explicitly classified.
- Add Supplier opened without crashing.
- Category selector and optional services were visible; selecting the available category displayed its service unchecked.
- The dialog was cancelled without saving. No supplier, invoice, financial or material write was performed for testing.
- The training catalog exposed one category, so choosing multiple actual categories was not demonstrated in this browser check; previous source tests cover that contract.
- Financial totals were displayed as unavailable; this refresh does not claim financial acceptance or activate financial writes.

Rollback remains available through `switch_preview_runtime_r2.py --rollback` in `/root/mezan-preview-upload-owner-r2-20261026`, subject to fresh inactive lease/current Git checks and exact candidate ownership. Exact prior supervisor bytes are retained in `supervisor.before`; the old runtime and failed first candidate remain preserved. Do not clear another task's lease or reset Git. If Production advances, re-review the rollback Git reference while preserving the configuration ownership checks.

PR1551 remains cancelled. Product classification policy is unchanged. APK work remains with its own task.
