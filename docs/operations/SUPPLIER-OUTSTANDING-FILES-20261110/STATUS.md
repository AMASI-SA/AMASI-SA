# Supplier outstanding files — 2026-11-10

Implementation checkpoint; NOT deployed, no APK built.

Latest owner instruction supersedes employee-date grouping for My Products only: supplier dispatch sent_at in Riyadh, remaining products and files, received/remaining counts, hide completed files, remaining-only download with visible busy state.

Backend adds opt-in supplier_dates=true on self progress routes. Existing clients and Monitoring keep employee date. No provider calls, writes, background timers or receiving bypasses. Exact owner/employee/current dispatch scope retained. Receipts use existing remaining-PDF receipt proof, including transferred receiving custody.

Validation: 48 tests passed (supplier progress, existing employee/monitoring dates including real route hydration, remaining PDF receipt filtering). Synthetic 30 total/25 received/5 remaining; fully received disappears; midnight Riyadh. Mobile actual TSX harness passes count/loading/rapid double tap/success/error unlock/completed hiding. This is not device or physical printer proof.

Mobile changes based on release source 3f0567991c20c1b229811ceb8b852d8bb040bf6c (Android76). Separate local worktree has older support files, exact touched source restored from that SHA; full build and device acceptance remain required. New client opts in; backend should precede client activation. No version/build artifacts claimed.

Remaining before release: independent review of query cost at production scale, reassignment/cancellation versus PDF membership parity, full current mobile source build, device acceptance, CI, governed source/intent release. Shipping PR1859 remains separate and unmodified.
