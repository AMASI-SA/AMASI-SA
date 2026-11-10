# Supplier outstanding files — 2026-11-10

Implementation checkpoint; NOT deployed. Android77 build is running, not yet accepted or delivered.

Latest owner instruction supersedes employee-date grouping for My Products only: supplier dispatch sent_at in Riyadh, remaining products and files, received/remaining counts, hide completed files, remaining-only download with visible busy state.

Backend adds opt-in supplier_dates=true on self progress routes. Existing clients and Monitoring keep employee date. No provider calls, writes, background timers or receiving bypasses. Exact owner/employee/current dispatch scope retained. Receipts use existing remaining-PDF receipt proof, including transferred receiving custody.

Validation: 50 tests passed (supplier progress, existing employee/monitoring dates including real route hydration, remaining PDF receipt filtering). Command: PYTHONPATH=.:tests python -m pytest --noconftest -p no:cacheprovider -q tests/test_supplier_progress_files.py tests/test_preparation_progress_dates.py tests/test_supplier_dispatch_remaining_pdf.py. Synthetic 30 total/25 received/5 remaining; actual PDF builder passes five units to renderer, then four after another receipt, then rejects fully received; dispatch stays on original day; midnight Riyadh. Mobile actual TSX harness passes count/loading/rapid double tap/success/error unlock/completed hiding. This is not device or physical printer proof.

Mobile source restored to full PR347 application source. Full TypeScript and focused CI passed. Fixed pagination dropping metadata-only supplier accounts. Existing route-return fixture still matched old progress URL; updated URL and supplier sent_at at a00a5eab2f98a63138eaeac9ada577468effccda (test-only, no APK input change); current-component local harness passes, full CI pending.

Android77 / 1.0.62 Expo build e4d1b852-d604-4c67-a5af-feb3dc0335a6 started from immutable 05c5c5feac3adb5dcda2a072422ba81506640501, profile mezan-web-assembly-pilot, production environment, internal APK, no store submission. JS bundle passed; Gradle running at last observation. https://expo.dev/accounts/amasi-sa/projects/amasi/builds/e4d1b852-d604-4c67-a5af-feb3dc0335a6 . No APK success or automatic OTA claimed. New client opts in; backend should precede client activation.

Remaining before release: review query cost at production scale and reassignment/cancellation versus PDF membership parity, finish native build and full CI, device acceptance, governed source/intent release. Shipping PR1859 remains separate and unmodified (all 14 CI runs passed on 8f6990cddf6fb874af4f7135d642ddbf6dda26ed). Emergent code-server authenticated this turn; no shared /app changes or release lease created. Must read fresh guard status before shared changes/rehearsal.
