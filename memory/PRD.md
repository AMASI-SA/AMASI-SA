# PRD — Hesab (تطبيق محاسبي ذكي لمنصة سلة)

## Original Problem Statement
أريد بناء تطبيق محاسبي ذكي للتجارة الإلكترونية يقوم بتحليل ملفات Excel المصدرة من منصة سلة واستخراج وتحليل البيانات المالية تلقائياً.

## Architecture
- **Backend**: FastAPI + Motor (MongoDB async) — JWT auth (cookies + bearer), openpyxl Excel parsing, xlsxwriter Excel export, reportlab + arabic-reshaper for PDF export, httpx for Snapchat Marketing API.
- **Frontend**: React 19 + React Router 7 + TailwindCSS + Shadcn/UI + Recharts + @phosphor-icons/react.
- **Database**: MongoDB collections: `users`, `settings`, `daily_costs`, `analyses`, `snapchat_connections`, `snapchat_ad_accounts` (multi-account selection — iteration 15), `snapchat_account_daily` (per-account, per-day spend with native + SAR + FX rate — iteration 15), `meta_connections`, `meta_daily_stats`.

## User Personas
1. **تاجر إلكتروني** يدير متجر على منصة سلة ويحتاج لتحليل الأرباح الحقيقية.

## Core Requirements (static)
- تحليل ملف Excel من سلة (المبيعات، عدد الطلبات، طرق الدفع، شركات الشحن).
- إدخال نسب عمولات الدفع وتكاليف الشحن من الإعدادات.
- حساب الأرباح الصافية بعد العمولات والشحن والإعلانات والمنتجات.
- تقارير تفصيلية لكل طريقة دفع ولكل شركة شحن.
- إضافة التكاليف اليومية (إعلانات سناب/تيك توك/إنستقرام + منتجات).
- حسابات منفصلة لكل مستخدم (auth + isolation).
- تصدير التقارير إلى PDF و Excel.

## ✨ FEATURE (2026-06 — Iteration 18) — **Dashboard Snapchat card: per-account TODAY breakdown**

**Merchant request**: "بدّل الصرف الشهري بكرت السناب لوحة التحكم إلى الصرف اليومي للسناب الثاني، مع تحديث إجمالي تكلفة الإعلانات بكرت لوحة التحكم بصرف جميع الحسابات الإعلانية."

**Implemented**:
- ✅ **Dashboard Snapchat card**: when 2+ Snapchat ad accounts are enabled, the "هذا الشهر" block is replaced by **"صرف اليوم — لكل حساب إعلاني"** with one cell per enabled account showing today's spend in SAR (and native currency if different). The header carries an account-count badge and an "Asia/Riyadh" timezone badge.
- ✅ When only 0/1 account is enabled, the **original Monthly block is preserved** as a graceful fallback (single-account merchants see no UX regression).
- ✅ **Total Ads Cost card** (`card-total_ads_cost` in `dashboardCards.js`) now correctly reflects ALL ad accounts — Snapchat (sum of every enabled account via `daily_costs.snapchat_ads` after iteration 17), TikTok (webhook + manual via iteration 16 fix), Meta. Verified live with seeded data: `total_ads_cost = 400.0` when Brand A=150 + Brand B=250.
- ✅ **Visual verification**: screenshot @ 1280x900 with 2 seeded accounts shows the per-account cards rendering correctly, zero horizontal overflow.

**New testids**: `snap-per-account-breakdown`, `snap-account-today-card-{ad_account_id}`.

**Data flow**: Dashboard now polls `/api/snapchat/accounts-summary` (added in iteration 15) in parallel with the existing summary endpoints — no new backend code needed.

---

## 🐛 BUG FIX (2026-06 — Iteration 17) — **Snapchat card dropping the 2nd account after legacy refresh**

**Reported by merchant**: "بطاقة اعلانات السناب في لوحة التحكم تعرض تكلفة الإعلانات من حساب [user_id] فقط ... بالبداية كان يعرض بشكل صحيح التكلفة من الحسابين الإعلانيين ولكن بعد التحديث مرتين نقصه صرف الحساب الثاني."

**Root cause**: TWO endpoints were writing to `daily_costs.snapchat_ads`:
1. `/snapchat/sync-all-accounts` (new, iteration 15) — wrote the SUM across all enabled accounts.
2. `/snapchat/daily-spend/bulk` (legacy single-account) — wrote ONLY the spend of `snapchat_connections.ad_account_id`, OVERWRITING the multi-account aggregate.

When the merchant hit the legacy refresh on the dashboard (which still pointed at `/daily-spend/bulk`), it silently wiped the second account's spend from the card. Each subsequent click kept the value pinned to a single account.

**Fix applied** (`/app/backend/snapchat_routes.py`):
- ✅ Added `_reaggregate_snap_daily(uid, date_str)` helper — the single source of truth for `daily_costs.snapchat_ads` and `snapchat_daily_stats`. Sums from `snapchat_account_daily` (per-account collection) across ALL of the user's accounts.
- ✅ Added `_ensure_legacy_account_tracked(uid, ad_id, ...)` — auto-upserts a `snapchat_ad_accounts` enabled row for the legacy account so the aggregation helper sees it (idempotent).
- ✅ Refactored legacy `/daily-spend/bulk`: now writes to `snapchat_account_daily` for the account being synced, then calls the helper. Never overwrites another account's data.
- ✅ Refactored `/sync-all-accounts`: replaced inline aggregation with calls to the same helper (DRY + guarantees both endpoints stay in sync forever).

**Verification**:
- ✅ Regression test `tests/test_snap_aggregation_no_overwrite.py` (2/2 pass) — simulates the exact bug sequence: seed 2 accounts → legacy refresh on account A twice → confirm B's spend STILL counted.
- ✅ Full Snapchat+TikTok+Meta suite: **68/68 pass**.

---

## 🐛 BUG FIX (2026-06 — Iteration 16) — **TikTok Dashboard Card was 0 even when campaigns were spending**

**Reported by merchant**: "تقرير التيك تك في لوحة التحكم أو بطاقة تكلفة الإعلانات لا تعرض أي بيانات على الرغم من أن الحملات تصرف بالوقت الحالي."

**Root cause investigation** (3 bugs in one report):
- **Bug A — multi-campaign-per-date overwrite** (`/api/dashboard/tiktok-summary`):  the line `tt_by_date = {r["date"]: r for r in tt_rows}` SILENTLY dropped all but the last campaign per date. Merchants running 2-3 active TikTok campaigns saw only 1/3 of their actual spend on the card.
- **Bug B — partial daily_costs coverage dropped webhook spend** (`/api/dashboard/tiktok-summary`):  `_agg()` iterated over `dc_spend_by_date.items()` only, then fell back to webhook ONLY when daily_costs contributed exactly `0.0`. As a result, **any merchant with even one old manual `daily_costs.tiktok_ads` row inside the range had ALL webhook spend for OTHER dates dropped**. Admin's card showed 73 SAR (= old manual 33 + 40) instead of the correct 423 SAR (= 73 + webhook 350.75).
- **Bug C — master `daily_ads_total` missed TikTok webhook entirely** (`/api/dashboard`): the `daily_ads_total` sum read `tiktok_ads` only from `daily_costs`, ignoring `tiktok_ads_daily` (where Make.com pushes). The "إجمالي تكلفة الإعلانات" card on Dashboard therefore undercounted TikTok by the full webhook amount for every merchant.

**Fix applied** (`/app/backend/server.py`):
- ✅ Multi-campaign aggregation: `tt_by_date` now accumulates spend+purchases+revenue across rows for the same date.
- ✅ Spend aggregation iterates the **union** of `tt_by_date` and `dc_spend_by_date` dates and uses `max(webhook, manual)` per date to avoid double-counting.
- ✅ `daily_ads_total` adds `max(tiktok_spend_from_tiktok_ads_daily, sum(daily_costs.tiktok_ads))` (no more silent drop).

**Verification**:
- ✅ Live admin card: before fix `last_30d.spend=73.0`, after fix `last_30d.spend=423.75` (correct: 33+40 manual + 350.75 webhook).
- ✅ Live admin dashboard: `daily_ads_total = 702.75`, `total_ads_cost = 702.75`, `tiktok_spend = 350.75` (was 0).
- ✅ Pytest: `tests/test_tiktok_dashboard_aggregation.py` — 4/4 new regression tests pass (locks in bug A/B/C).
- ✅ Full Snapchat+Meta+TikTok suite: 63/63 pass.

---

## ✅ COMPLETED & VERIFIED (2026-06 — Iteration 15) — **Snapchat Multi-Account Expansion**
**Status**: 🟢 Production-ready. Tested end-to-end by `testing_agent_v3_fork`.

**Acceptance — 100% PASS (7/7 new + 62/62 regression):**
- ✅ **Multi-account selection**: `GET/PUT /api/snapchat/selected-accounts` — merchant can enable/disable any number of Snapchat ad accounts simultaneously via checkbox UI in Settings. Removing an account marks it `enabled=False` (not deleted) so re-enabling preserves sync history.
- ✅ **DB schema**: new `snapchat_ad_accounts` collection (unique `(user_id, ad_account_id)`) and `snapchat_account_daily` collection (unique `(user_id, ad_account_id, date)` + secondary `(user_id, date)` index). Indexes registered in `server.py` startup.
- ✅ **Asia/Riyadh enforcement**: all daily-spend windows = `00:00 → 23:59 Asia/Riyadh` (HOUR granularity to bypass Snap's DAY-PDT constraint). PDT is NEVER used for storage or display.
- ✅ **Currency tracking**: each `snapchat_account_daily` row stores `spend_native` + `currency_native` + `fx_rate` + `spend_sar` (alongside legacy `spend` alias). USD→SAR conversion uses SAMA peg 3.75. Per-account UI shows BOTH native and SAR side-by-side.
- ✅ **Dashboard card aggregation**: `POST /api/snapchat/sync-all-accounts` iterates over all enabled accounts and (1) writes per-(account,date) rows, (2) aggregates the cross-account sum back into legacy `daily_costs.snapchat_ads` so the existing dashboard card auto-updates without any other code change.
- ✅ **New detail page `/snapchat-accounts`**: per-account cards with today / month / 30d spend in native+SAR+FX-rate, last-sync badge, "مزامنة كل الحسابات" button. Cross-account totals card on top. Empty-state with CTA to settings.
- ✅ **Sidebar nav**: new "حسابات Snapchat" link (`nav-snapchat-accounts` testid).
- ✅ **OAuth untouched**: existing connect/disconnect/`/snapchat/config`/`/select-adaccount` (back-compat) all still pass.
- ✅ **Friendly Arabic errors**: `sync-all-accounts` without OAuth → "حساب سناب غير مربوط. اربطه من الإعدادات." (no JSON / OAuthException leak).
- ✅ **Mobile responsive**: zero horizontal page scroll at 390x844 on `/settings` and `/snapchat-accounts`.

**New endpoints**:
- `GET /api/snapchat/selected-accounts` — list enabled accounts.
- `PUT /api/snapchat/selected-accounts` — replace the enabled set.
- `POST /api/snapchat/sync-all-accounts` — sync all enabled, write per-account daily rows + aggregate to `daily_costs`.
- `GET /api/snapchat/accounts-summary` — per-account today/month/30d spend (native + SAR + FX).

**Test report**: `/app/test_reports/iteration_15.json`. Pytest suite: `/app/backend/tests/test_snapchat_multi_account.py` (7 tests).

---

## ✅ COMPLETED & VERIFIED (2026-06 — Iteration 14) — **خيار A: Meta Token Exchange**
**Status**: 🟢 Production-ready. Tested end-to-end by `testing_agent_v3_fork`. Awaiting merchant green-light before starting **خيار B (Full OAuth flow)**.

**Acceptance — 100% PASS:**
- ✅ Backend `POST /api/meta/exchange-token` — converts a Short-lived Graph API Explorer token (1-2h) into a 60-day Long-lived token via Meta's `fb_exchange_token` grant, persists the new token + `token_expires_at`, clears any prior expired status.
- ✅ Settings UI — blue dashed-border section "تحويل تلقائي إلى Long-lived Token (60 يوم)" inside the `meta-credentials-details` accordion. Button correctly disabled when input is empty. SecretField masking on the short-lived input (no layout break at 390 or 1280 widths).
- ✅ Friendly Arabic errors on every edge case (empty, short, missing app creds, bad ad_account_id, fake creds rejected by Meta) — verified ZERO raw JSON / `OAuthException` / `[object Object]` leaks.
- ✅ Expiry countdown banner (`meta-token-expiry-info`) — colour-coded emerald/amber/red based on days remaining.
- ✅ Backend regression: **13/13 pass** (6 new in `test_meta_token_exchange.py` + 7 in `test_meta_friendly_errors.py`) + **6/6 iteration-13 regression pass** — no regressions.
- ✅ Mobile (390x844) and desktop (1280x800) — zero horizontal page scroll.

**Reports**: `/app/test_reports/iteration_14.json` + pytest XML at `/app/test_reports/pytest/iteration_14_meta_exchange.xml`.

**Pending (NOT started — awaiting merchant approval):**
- ⏸️ **خيار B — Full OAuth "اربط مع Facebook"**: one-click Meta login flow that eliminates the need for the merchant to copy ANY token (even short-lived). Will reuse `meta_connections` schema and add `redirect_uri` + state JWT exactly like the Snapchat OAuth flow.

**Doc note**: SecretField testids are `{prefix}-input-masked` (hidden) and `{prefix}-textarea` (revealed) — NOT `{prefix}-input`. Update spec for future test agents.

## Implemented (2026-05 — Meta Token Exchange: Short-lived → Long-lived auto-conversion)
- 💡 **Merchant request**: Avoid having to re-paste a fresh 60-day Long-lived token every 2 months. Allow the merchant to paste any Short-lived token (1-2 hour, easier to obtain from Graph API Explorer) and have us convert it automatically.
- ✅ **Backend**:
  - **New helper `_exchange_short_for_long_lived(app_id, app_secret, short_token)`** in `meta_routes.py`: calls Meta's official `GET /v18.0/oauth/access_token?grant_type=fb_exchange_token` endpoint. Returns the new 60-day token + `expires_in` (in seconds).
  - **New `POST /api/meta/exchange-token`** endpoint accepting `{short_lived_token, app_id?, app_secret?, ad_account_id?}` (last 3 fall back to the stored config when blank — typical update flow). Validates: minimum 20-char short token + required app credentials (with friendly Arabic errors). On success: saves the new `access_token` + computes `token_expires_at = now + expires_in seconds` + clears any prior `expired` status.
  - **Response** includes: `access_token_masked` (first 10 + bullets + last 6), `token_expires_at` (ISO), `token_expires_in_days` (≈60). We **never** return the full token to the browser.
  - **`/meta/config`** now exposes `token_expires_at` + `token_exchanged_at` so the UI can render countdowns.
- ✅ **Frontend (Settings.jsx)**:
  - **New visually-distinct blue dashed-border section** "تحويل تلقائي إلى Long-lived Token (60 يوم)" right above the manual token field.
  - **`<SecretField>` for the short-lived input** (paste long token without breaking layout) + helper text linking to Graph API Explorer + permission list.
  - **`data-testid="meta-exchange-token-btn"`** button. Disabled while input empty (UX guard). Spin icon while loading. On success, toast shows: `"✓ تم التحويل وحفظ التوكن الجديد (EAA****ABC). صالح حتى 7 يوليو 2026 (~60.0 يوم)"`.
  - **Expiry countdown** (`data-testid="meta-token-expiry-info"`): live calculation from `token_expires_at`. Color-coded — green when >7 days, amber 1-7 days ("⚠️ متبقي N يوم فقط — جدّد الآن"), red when expired.
  - Manual access-token field label updated to "Access Token (Long-lived) — أو الصق توكن جاهز يدوياً" + helper updated to point at the new auto-flow.
- ✅ **Error handling**: All edge cases return friendly Arabic — never raw Pydantic JSON or Meta error bodies. Tested:
  - `short_lived_token=""` → "Short-lived token قصير جداً أو فارغ — انسخ التوكن كاملاً من Graph API Explorer."
  - No stored app_id/secret → "Meta App ID و App Secret مطلوبان للتحويل. احفظهما أولاً..."
  - Bogus token + creds → Friendly Meta classification (typically "تعذّرت المزامنة...").
- ✅ **Tested**: smoke screenshot confirms section renders cleanly, button is reactive (disabled-when-empty), no horizontal scroll, all testids present. 28/28 backend pytest regression pass.


- 🐛 **Issue**: Long Meta access tokens (200+ chars) and Snap client secrets caused horizontal page scroll on mobile + overflowed cards + made the Settings page feel cluttered.
- ✅ **New component `SecretField.jsx`** (`/app/frontend/src/components/SecretField.jsx`):
  - **Masked preview** by default: shows first 10 chars + bullets + last 6 chars (max ~22 chars on screen).
  - **👁 عرض / 🙈 إخفاء** toggle: expands the field into a wrappable `<textarea>` for editing.
  - **📋 نسخ** button (clipboard API) + **🗑 مسح** button (clears field, doesn't touch server).
  - CSS `overflow-wrap: anywhere; word-break: break-all;` ensures no horizontal scroll even with extreme-length tokens.
  - Fully responsive — buttons flex-wrap under field on phones.
  - Accepts `existingMask` prop so backend-returned masks ("EAA****ABC") are shown as placeholder hints.
  - Optional `statusBadge` prop for inline status pills.
- ✅ **New `<StatusBadge/>`** component (also exported from SecretField.jsx):
  - 🟢 صالح / 🟡 يحتاج تجديد قريباً / 🔴 منتهي الصلاحية / صلاحيات ناقصة / حساب غير صالح / تم تجاوز الحد / خطأ شبكة.
  - Driven by Meta's `connection_status` already returned by `/api/dashboard/meta-summary` & `/api/meta/config`.
  - Rendered inline next to "Access Token (Long-lived)" label.
- ✅ **New top-level Settings section "🔐 بيانات الربط الحساسة"** wraps both Snapchat AND Meta integration cards inside two collapsible `<details>` accordions (`data-testid="snap-credentials-details"` and `meta-credentials-details`). Each accordion summary shows the platform badge + connection-status pill so the merchant sees state at a glance without expanding.
- ✅ **Settings.jsx hardening**:
  - All `grid` containers got `min-w-0` (prevents flex/grid blowout from long children).
  - Both card wrappers got `overflow-hidden` + `p-4 sm:p-6` (smaller padding on phones).
  - Meta App Secret + Meta Access Token + Snap Client Secret → ALL converted to `<SecretField>` (testids: `meta-app-secret-*`, `meta-access-token-*`, `snap-client-secret-*`).
  - Redirect URI input got `overflowWrap: anywhere` + `wordBreak: break-all` (long URLs no longer push the card width).
- ✅ **Tested** @ 390x844 mobile viewport:
  - `document.documentElement.scrollWidth === clientWidth === 390` → **zero horizontal scroll**.
  - All new testids present and functional (toggle, copy, clear).
  - Snap accordion (closed) + Meta accordion (open) render correctly together.


- 🐛 **Issue**: Snapchat's `DAY` granularity stats require `start_time` to be midnight in the **ad-account's native TZ** (usually Pacific). For Saudi merchants this meant "today" on Snapchat ran from 11:00 AM → 11:00 AM Riyadh time — not 00:00 → 23:59 like every other Saudi business measures their day.
- 💡 **Merchant requirement**: "اعتماد توقيت السعودية Asia/Riyadh في احتساب اليوم الإعلاني." Even if Snapchat internally tracks the day in PT, our dashboard must show the Riyadh business day.
- ✅ **Backend technique**: switched from `granularity=DAY` to `granularity=HOUR`. HOUR has NO TZ alignment constraint — we can request `start_time = 2026-06-01T00:00:00+03:00` and `end_time = 2026-06-02T00:00:00+03:00`, and Snapchat returns 24 hourly buckets which we sum. The resulting total is the EXACT Riyadh-day spend regardless of where the ad account is hosted.
- ✅ **Both endpoints updated**:
  - `GET /api/snapchat/daily-spend?date=` (single-date, used by DailyCosts page and Dashboard refresh button).
  - `POST /api/snapchat/daily-spend/bulk` (range mode, used by "تحديث آخر 7 أيام" etc).
  - Both now use `granularity=HOUR` for `spend` AND for conversion metrics (Phase 2).
- ✅ **Response diagnostics**:
  - `business_timezone: "Asia/Riyadh"` (always)
  - `aggregation_method: "hourly_riyadh"`
  - `ad_account_timezone` (informational, e.g. `"America/Los_Angeles"`)
  - `snap_day_start_riyadh` / `snap_day_end_riyadh` (always 00:00 → 24:00 Riyadh strings).
- ✅ **Frontend (`Dashboard.jsx`)**:
  - Banner color/text refreshed from amber (warning) to **green (confirmation)**: `"✓ يتم احتساب اليوم حسب توقيت السعودية (2026-06-01 00:00 → 2026-06-02 00:00) • TZ حساب الإعلانات على Snap: America/Los_Angeles — لكننا نجمع الصرف ساعةً بساعة لتغطية يوم الرياض كاملاً (00:00 → 23:59)."`
  - Zero-spend toast now says: `"تم الجلب — صرف يوم 2026-06-01 بتوقيت الرياض (00:00 → 24:00) = 0.00 ر.س. تأكد من وجود حملات نشطة أو انتظر بدء صرف اليوم."` — no longer references Pacific/PT.
- ✅ **DB schema**: `daily_costs.date` continues to be the Riyadh business date (no schema change). Reports/`/reports/ads` automatically show Riyadh-aligned data.
- ✅ **Tested**: 28/28 backend regression pass. Curl admin (no creds) returns the friendly Arabic error.


- 💡 **User insight**: merchant reported that Snapchat's "today" doesn't align with Riyadh midnight — for their account, the day appears to start at ~12:00 PM Riyadh time. This is because Snapchat's DAY granularity uses the **ad account's own timezone** (often Pacific or UTC), not Riyadh's.
- ✅ **Backend (`snapchat_routes.py`)**:
  - `GET /api/snapchat/daily-spend?date=` response now includes 3 new diagnostic fields: `ad_account_timezone` (e.g. `"America/Los_Angeles"`), `snap_day_start_riyadh` (e.g. `"2026-06-01 11:00"`), `snap_day_end_riyadh` (e.g. `"2026-06-02 11:00"`).
- ✅ **Frontend (`Dashboard.jsx`)**:
  - **New `snapDayInfo` state** cached after each refresh. Renders a persistent yellow info banner inside the Snap card: `"ℹ️ TZ حساب الإعلانات: America/Los_Angeles • "يوم Snap" يبدأ 2026-06-01 11:00 وينتهي 2026-06-02 11:00 بتوقيت الرياض."`
  - Zero-spend toast also surfaces the same TZ boundary so the merchant immediately understands why today=0 ("التزال بداية اليوم لم تبدأ بعد بتوقيت Snap").
  - `data-testid="snap-day-info-banner"` for testability.


- 🐛 **Issue**: even after the two-phase bulk fix, the Dashboard refresh button still used `POST /snapchat/daily-spend/bulk` which involves more moving parts than necessary for a single-day refresh.
- 💡 **User insight**: the DailyCosts page already has a working "جلب من سناب" button that has been reliable in production. Just port that exact flow to Dashboard.
- ✅ **Fix in `Dashboard.jsx`**:
  - **`snap-refresh-today-btn`** now calls `GET /snapchat/daily-spend?date=YYYY-MM-DD` (single-date, spend-only, proven reliable) then manually upserts the value into `daily_costs` via `POST /daily-costs` — preserving any other fields on the same date (snapchat_ads_2, tiktok_ads, instagram_ads, google_ads, product_costs, notes).
  - **`refresh-all-ads-btn`** (Snap branch) also switched to the same single-date flow for consistency.
  - Friendly Arabic error toasts retained: covers `Unsupported Stats Query`, `invalid_token / 401`, `permission / 403`, "اربط Snapchat" empty-state. Never shows raw JSON.
  - Distinguishes zero-spend (info toast: "لا توجد حملات نشطة أو لم يبدأ الصرف بعد") from non-zero success.
  - FX-conversion note shown when the ad-account currency ≠ SAR (matches DailyCosts UX).
- ✅ **Verified**: smoke screenshot confirms toast for admin-without-creds shows clean Arabic: "حساب سناب غير مربوط. اربطه من الإعدادات.". Backend unchanged (two-phase bulk fix from previous iteration still in place for legacy callers).


- 🐛 **Issue**: `POST /api/snapchat/daily-spend/bulk` returned raw JSON error `{"request_status":"ERROR","debug_message":"Unsupported Stats Query"…}` to the merchant on every refresh attempt.
- 🔍 **Root cause**: We were requesting `spend + conversion_purchases + conversion_purchases_value` in a single `/adaccounts/{id}/stats` call. Snapchat Marketing API rejects this combo because conversion metrics (a) require explicit `swipe_up_attribution_window` + `view_attribution_window` parameters, AND (b) are sometimes unavailable at ad-account level depending on the Pixel setup. Result: the entire request fails (including spend), so even the `spend` value never reached `daily_costs`.
- ✅ **Fix in `snapchat_routes.py`**:
  - **Two-phase request strategy**: Phase 1 fetches `fields=spend` only (always supported on `/adaccounts/{id}/stats`). Phase 2 attempts to fetch `conversion_purchases + conversion_purchases_value` with the required attribution windows (`swipe_up=28_DAY`, `view=1_DAY`). If Phase 2 fails (Pixel inactive, account-level metrics blocked, etc), we silently log and continue with `purchases=0` and `revenue=0` — spend still saves correctly.
  - **Error parsing**: when Phase 1 fails, we now extract Snapchat's `debug_message` field from the JSON response instead of returning the whole body verbatim (no JSON leak).
- ✅ **Fix in `Dashboard.jsx`**:
  - Toast now translates well-known Snapchat error patterns into Arabic: `Unsupported Stats Query`, `invalid_token / 401`, `permission / 403`, `granularity / start time` — each gets a tailored Arabic message with a remediation hint. Generic errors get a truncated friendly wrapper. No raw JSON / `request_id` strings leak to the user.
- ✅ **Tested**: 28/28 backend regression pass (`test_unified_ads_report.py` + `test_operating_expenses.py` + `test_meta_friendly_errors.py`). Snapchat-specific tests passing.


- 🐛 **Issue 1 — Snapchat refresh = 0**: Investigation revealed the refresh path was correct (Riyadh date), but the UI gave no diagnostic when Snapchat API legitimately returned `spend=0` (TZ mismatch on ad account, no active campaigns, etc). Fix: backend response now includes `ad_account_timezone`; frontend distinguishes 3 outcomes — success with spend, fetched-but-zero (info toast with TZ hint), and hard error (friendly Arabic).
- ✅ **TikTok card always visible** — removed `if (totals.tiktok_spend > 0 || ...)` gating. Now mirrors Snap/Meta layout exactly: Today (spend/orders/revenue/ROAS) + Month (same 4) + 30-day sparkline + "آخر تحديث" + footer link to `/reports/ads`.
- ✅ **`tiktok-refresh-btn`** — calls new `GET /api/dashboard/tiktok-summary` and re-renders the card (TikTok Marketing API direct integration deferred to a future iteration; for now it re-aggregates the existing Make.com webhook data).
- ✅ **`tiktok-empty-state`** — friendly Arabic prompt with link to `/make-webhook` when `has_data=false`.
- ✅ **New backend endpoint `GET /api/dashboard/tiktok-summary`** — mirrors snap/meta contracts: `{today, month, last_30d, history[30], last_fetched_at, source, has_data}` all in Riyadh time.
- ✅ **Total Ads Cost now includes Meta** — `daily_ads_total` and `total_ads_cost` aggregate over `daily_costs.{snapchat_ads, snapchat_ads_2, tiktok_ads, instagram_ads, google_ads}` **PLUS** `meta_ads_daily.spend` (was missing). Verified via SEED test: +300 SAR Meta row increases both fields by exactly 300.
- ✅ **New `meta_spend / meta_purchases / meta_revenue / meta_roas`** in `/api/dashboard` totals.
- ✅ **`refresh-all-ads-btn`** (gradient yellow→pink→blue) at top of Dashboard — orchestrates Snap + Meta + TikTok in parallel via `Promise.all`. Each platform fails independently (NEVER blocks others, NEVER clears data). Consolidated toast: `"تحديث جزئي (1/3) • ✓ Snapchat: 5 سجل • ✗ Meta: انتهت صلاحية… • ✓ TikTok: تم تحديث البيانات المحلية"` — no JSON / `[object Object]` leaks.
- ✅ **All cards show `آخر تحديث`** with Riyadh-formatted timestamp.
- ✅ **Code review fix** (from testing-agent): `refreshAllAds` fallback now falls through to `tiktokSummary?.today?.date` before resorting to `new Date()` (UTC) — guarantees Riyadh-aligned dates even when only one summary loaded.
- ✅ **Testing**: testing_agent_v3_fork → **43/43 backend pass** (6 new + 37 regression) + **10/10 Playwright frontend pass**. Report: `/app/test_reports/iteration_13.json`.


- 🐛 **Issue**: clicking the "تحديث فوري للصرف اليوم" button on the Meta card surfaced a raw JSON `OAuthException code 190 Session has expired` to the merchant when their Access Token was no longer valid. No clear path to fix it.
- ✅ **Backend (`meta_routes.py`)**:
  - New `_classify_meta_error(text) → (status, friendly_arabic_msg)` covering: expired-token (code 190 / "session expired" / "access token invalid"), permission denied (code 200 / ads_read), invalid ad account (code 100), rate-limited (code 17), network/timeout, and generic fallback. Each returns a hand-translated Arabic message.
  - New `_verify_meta_credentials(ad_account, token)` — lightweight ping (calls `/act_X?fields=id,name,...`) to test creds without burning the heavier `/insights` quota.
  - New `_set_status(user_id, status, last_error)` — persists `connection_status`, `last_error_message`, `last_error_at` in `meta_connections`.
  - `POST /api/meta/sync` — on Meta error, raises HTTP **401** when status="expired" (else 400) with `detail = {message, status, raw}` so the frontend can branch. **CRITICAL**: existing `meta_ads_daily` rows are NEVER cleared on token failure — historical spend stays visible behind the banner.
  - `POST /api/meta/auto-sync-if-stale` — same classification path but silent (background job, no exception raise).
  - **New endpoint `POST /api/meta/test-connection`** — accepts the same body shape as `PUT /meta/config`, verifies against Meta API, and persists the credentials **ONLY IF the test passes**. Returns `{ok, message, account: {id, name, currency, timezone}, saved: true}` on success; 400 with the friendly Arabic on failure.
  - `GET /api/meta/config` now exposes `connection_status`, `last_error_message`, `last_error_at`.
  - `GET /api/dashboard/meta-summary` now exposes the same 3 fields so the Dashboard banner can render reactively.
- ✅ **Frontend (`Dashboard.jsx`)**:
  - Meta card refresh button now handles **both** detail shapes (object with `{message, status}` and legacy plain string) via a typeof guard. Raw JSON / `[object Object]` can no longer leak.
  - New **expired banner** `data-testid="meta-expired-banner"` (red, prominent) above the KPI grid — shown only when `connection_status === "expired"`. Contains the Arabic warning + **`meta-update-link-btn`** linking to `/settings`.
  - Secondary `data-testid="meta-warn-banner"` (amber, softer) for other non-ok statuses (rate-limit, permission, etc).
  - After a failed sync, `fetchMetaSummary()` is called so the banner appears immediately without a page refresh.
- ✅ **Frontend (`Settings.jsx`)**:
  - Central error formatter `fmtMetaErr(e, fallback)` used in every Meta catch block — handles both detail shapes uniformly.
  - **New button `data-testid="meta-test-connection-btn"`** ("اختبار الاتصال", amber) — calls `/test-connection`; success toast shows account name, failure toast shows the friendly Arabic message. Save button relabeled to **"حفظ بدون اختبار"** so the merchant understands the distinction.
  - **New banner `data-testid="meta-settings-expired-banner"`** (red, with timestamp) when `connection_status === "expired"`, plus secondary amber warn-banner for other errors.
  - Token input placeholder + helper text now coach the merchant: "ألصق التوكن الجديد ثم اضغط اختبار الاتصال".
  - Token + secret inputs are auto-cleared after a successful save/test (avoids accidental resubmission).
- ✅ **Testing**: testing_agent_v3_fork → **37/37 pytest pass** (7 new in `test_meta_friendly_errors.py` + 30 regression) + **7/7 Playwright frontend checks pass**. Exact Arabic string `"انتهت صلاحية ربط Meta Ads، يرجى تحديث Access Token من الإعدادات."` verified to render from both `/test-connection` and the Dashboard refresh flow. Zero raw-JSON / `[object Object]` leaks. Report: `/app/test_reports/iteration_12.json`.


- 🐛 **Root cause**: The dashboard refresh button correctly **upserted** Snapchat spend with `$set` (overwrite, NOT increment), but the `date` used as the upsert key disagreed with the date the dashboard read:
  - **Writer** (refresh button → `/snapchat/daily-spend/bulk`): used the browser's *local* date or the *Snapchat ad account TZ* (typically Asia/Riyadh = UTC+3).
  - **Reader** (`/dashboard/snapchat-summary`): used `datetime.now(timezone.utc).date()`.
  - **Effect**: Between 21:00 UTC (00:00 Riyadh) and 23:59 UTC (02:59 Riyadh) every day, the writer saved under tomorrow's date (Riyadh's new day) while the reader still queried yesterday's date (UTC's not-yet-rolled-over day) → `today.spend = 0` for ~3 hours each night.
- ✅ **Fix in `server.py`**: introduced module-level `RIYADH_TZ = ZoneInfo("Asia/Riyadh")` (with UTC+3 fallback) plus helpers `_local_today_iso()` and `_local_today_date()`. Replaced all `datetime.now(timezone.utc).date()` calls in `/dashboard/snapchat-summary` and `/dashboard/meta-summary` (today_str, month_start, d30_start_str, and the 30-day history loop) with the Riyadh-based variant.
- ✅ **Fix in `meta_routes.py`**: same approach — `_today_riyadh()` helper, used by `POST /api/meta/sync` and `POST /api/meta/auto-sync-if-stale` so "days=1" actually fetches today's Riyadh date (was UTC).
- ✅ **Fix in `Dashboard.jsx`**: Snapchat refresh button now reads `todayStr = snapSummary.today.date` (the canonical Riyadh date from the backend) instead of computing it from `new Date()`. Guarantees writer and reader agree even when the merchant's browser is in a different timezone (e.g. team member abroad).
- ✅ **Note on `snapchat_routes.py`**: when explicit `from_date/to_date` are sent (the typical case from the dashboard refresh button), they are honored verbatim — `$set` overwrites the row instead of inserting a duplicate. Added a clarifying comment block. The "days" fallback still uses ad-account TZ (Snapchat API requires it for DAY granularity).
- ✅ **Verification**:
  - Curl regression test: POST daily-costs `{snapchat_ads: 100}` → GET dashboard.today.spend = 100; POST again with `{snapchat_ads: 250}` → dashboard.today.spend = **250 (overwrite confirmed, not 350)**.
  - Pytest regression: **206/206 backend tests pass** (no regressions on any existing test).


- ✅ **Sidebar transformed into a slide-in drawer on mobile** (<1024px) while remaining fixed on desktop (≥1024px). Uses `translate-x-full` ↔ `translate-x-0` transition with `lg:translate-x-0` always winning on desktop.
- ✅ **New mobile top header** (`data-testid="mobile-header"`) — hidden on desktop (`lg:hidden`) — contains compact logo + `data-testid="mobile-menu-btn"` hamburger button on the left side.
- ✅ **`Layout.jsx` rewritten**: manages `mobileOpen` state; `useEffect` on `location.pathname` auto-closes the drawer on route changes; another `useEffect` locks `document.body.overflow="hidden"` while drawer is open (with cleanup); replaced fixed `ps-64` with `lg:ps-64` (zero padding on mobile).
- ✅ **Sidebar drawer features**: full-screen `bg-black/50` backdrop (`data-testid="sidebar-backdrop"`) closes drawer on tap; in-drawer X close button (`data-testid="sidebar-close-btn"`); clicking any nav link auto-closes drawer; proper z-index layering (sidebar=z-50, backdrop=z-40).
- ✅ **Dashboard cards adapted**: Snap/Meta/TikTok section headers use `flex-col sm:flex-row`; refresh buttons are `w-full sm:w-auto`; container padding `p-4 sm:p-6`; recent-analyses table wrapped in `overflow-x-auto -mx-4 sm:mx-0 px-4 sm:px-0` with `min-w-[640px]`.
- ✅ **AdsReport** (`/reports/ads`): h1 scales `text-3xl sm:text-4xl lg:text-5xl`; refresh button `w-full md:w-auto`; combined KPIs `grid-cols-2 sm:grid-cols-3 lg:grid-cols-6`; platform cards stack on mobile; comparison table already had `overflow-x-auto`.
- ✅ **Reports** (`/reports`): h1 mobile-friendly; payments/shipping tables wrapped in `overflow-x-auto` with `min-w-[480px]`/`min-w-[400px]`.
- ✅ **OperatingExpenses**: h1 mobile-friendly; `oe-tabs` now `overflow-x-auto` with `whitespace-nowrap` buttons (horizontal scroll instead of wrap); `TableWrap` adds `min-w-[640px]` + edge-to-edge `-mx-4 sm:mx-0 px-4 sm:px-0`; section `Card` add-button `w-full sm:w-auto`.
- ✅ **Settings**: payment-methods-list and shipping-companies-list wrap their 12/14-column grid in `overflow-x-auto -mx-6 sm:mx-0 px-6 sm:px-0` with `min-w-[640px]`/`min-w-[700px]` on inner rows; save-settings-btn `w-full md:w-auto`.
- ✅ **Login/Register**: already responsive (`w-full lg:w-1/2`) with hero panel `hidden lg:flex` — verified.
- ✅ **Testing**: testing_agent_v3_fork verified 100% at BOTH 390x844 (iPhone-12) AND 1280x800 (desktop) — zero horizontal page scroll on any tested route; drawer slide/backdrop/auto-close/scroll-lock all confirmed; **26/26 backend pytest regression pass**. Report: `/app/test_reports/iteration_11.json`.


- ✅ **Snapchat & Meta cards on dashboard simplified to a unified 4+4 layout** per merchant request: "الكرت في Dashboard يكون سريع وواضح — كم صرفنا اليوم؟ كم طلب جاء؟ كم مبيعات؟ كم العائد؟".
  - **Today section** (4 cards on each): صرف اليوم (ر.س) · طلبات اليوم · مبيعات اليوم (ر.س) · ROAS اليوم.
  - **Month section** (4 cards on each): الصرف الشهري (ر.س) · طلبات الشهر · مبيعات الشهر (ر.س) · ROAS الشهر.
  - ROAS = sales ÷ spend (rounded 2dp), shows `—` when spend=0, color flips emerald ≥2x else amber.
- ✅ **Meta card cleanup — removed from dashboard** (they remain only on `/reports/ads`):
  - `meta-cpa-month` (CPA tile) ❌
  - `meta-performance-row` containing CPC, CPM, CTR, Impressions, Clicks ❌
  - `meta-campaigns-table` ❌
- ✅ **Snapchat card cleanup**: removed inline `≈ $X` USD conversion text from `snap-spend-today` / `snap-spend-month` / 30-day spend total (merchant operates in SAR only).
- ✅ **Unified instant-refresh buttons** — both cards now have the same UX:
  - Snap: `snap-refresh-today-btn` → `POST /api/snapchat/daily-spend/bulk` with today=today.
  - Meta: `meta-sync-now-btn` text changed from "مزامنة Meta الآن" to "تحديث فوري للصرف اليوم" → `POST /api/meta/sync` with `{days: 1}` (was 30).
  - Subtitle on Meta updated: "ربط مباشر مع Meta Marketing API — اضغط الزر للتحديث الفوري لصرف اليوم".
  - Empty state on Meta updated: prompts user to click "تحديث فوري للصرف اليوم" (was "مزامنة Meta الآن").
- ✅ **New footer link on both cards** → `/reports/ads`:
  - `snap-card-details-link` and `meta-card-details-link` with text "التفاصيل (CPC / CPM / CTR / الحملات) في تقرير الإعلانات الموحَّد ←".
- ✅ **30-day sparkline preserved** on both cards (compact, not "campaign details" — kept for at-a-glance trend).
- ✅ **Testing**: testing_agent_v3_fork verified frontend 100% — all required testids present, all removed testids confirmed absent, network panel confirms `{days: 1}` is sent on Meta refresh. Backend pytest regression: **21/21 pass** (test_unified_ads_report.py + test_operating_expenses.py). Iteration report: `/app/test_reports/iteration_10.json`.


### P0 — Meta Ads dashboard card cleanup
- ✅ **Removed all Make.com references** from the Meta Ads section header subtitle, replacing "تتم المزامنة يومياً عبر Marketing API" with "ربط مباشر مع Meta Marketing API — اضغط الزر للتحديث الفوري".
- ✅ **Replaced the Make.com empty-state** with a direct-integration prompt: `data-testid="meta-empty-state"` directs the user to the Settings page with a primary CTA button (`meta-go-settings-btn` → `/settings`) instead of asking them to set up a Make.com Scenario.
- ✅ **Added CPM card** to the Meta performance row (was missing). The row is now 5 cards: CPC, CPM, CTR, Impressions, Clicks (test-ids: `meta-cpc-month`, `meta-cpm-month`, `meta-ctr-month`, `meta-impressions-month`, `meta-clicks-month`).
- ✅ Meta `Sync Now` button (`meta-sync-now-btn`) calls `POST /api/meta/sync` directly — returns user-friendly Arabic error toast when Meta credentials aren't configured.

### P1 — Unified Ads Report page (`/reports/ads`)
- ✅ **New backend endpoint** `GET /api/reports/ads?from_date=&to_date=` — JWT-protected. Returns `{range, platforms[3], combined, series}` where each platform exposes `spend / impressions / clicks / purchases / revenue / cpc / cpm / ctr / cpa / roas` and `series` is a per-day cross-platform spend array.
- ✅ **Backend math** (server-side, zero-guarded, rounded to 2 dp): `cpc = spend / clicks`, `cpm = (spend / impressions) × 1000`, `ctr = (clicks / impressions) × 100`, `cpa = spend / purchases`, `roas = revenue / spend`.
- ✅ **Data sources**: Snapchat (daily_costs.snapchat_ads + snapchat_ads_2 + snapchat_daily_stats Pixel revenue/orders), TikTok (tiktok_ads_daily), Meta (meta_ads_daily).
- ✅ **New frontend page** `/reports/ads` (`pages/AdsReport.jsx`) with 5 sub-components:
  - **CombinedTotals** header card (6 KPIs across all platforms)
  - **PlatformCard ×3** — Snapchat (yellow theme), TikTok (black), Meta (blue) — each showing full 10-metric breakdown
  - **DailySpendChart** — Recharts LineChart with 3 lines comparing daily spend
  - **ComparisonTable** — 10 metric rows × (3 platforms + Total)
  - **RoasComparison** — Recharts BarChart of ROAS per platform (renders only when ≥1 platform has positive ROAS)
- ✅ **Date pickers** (`ads-report-from-date` / `ads-report-to-date`) default to month-to-date and refetch on change. Manual refresh button (`ads-report-refresh-btn`).
- ✅ **Reports page entry-point** — added prominent "تقرير الإعلانات الموحَّد" link (`reports-ads-link`) in the Reports header.
- ✅ **5 new pytest tests** in `test_unified_ads_report.py` (empty-state shape, Snapchat ingestion, TikTok ingestion + derived-metric math, date-range filtering, 3-platform combined math). **206/206 backend tests pass.**
- ✅ **Testing agent verified**: 100% on backend + 100% on frontend (all test-ids functional, both charts render, comparison table has exactly 10 rows).

## Implemented (2026-05 — Prepaid Expenses / المصروفات المدفوعة مقدماً)
- ✅ **New standalone accounting section** inside `/operating-expenses` — *not* merged with rentals per user request. Order of tabs is now: salaries → rentals → **prepaid** → daily → report.
- ✅ **Six sub-types** (PREPAID_TYPES whitelist):
  - 🚗 `vehicle_insurance` — تأمين السيارات
  - 👷 `worker_insurance` — تأمين الموظفين
  - 🪪 `iqama_visa` — الإقامات والتأشيرات
  - 📜 `government_license` — الرخص والتصاريح الحكومية
  - 🔁 `annual_subscription` — الاشتراكات السنوية
  - 📦 `other` — أخرى
- ✅ **Each record**: type, beneficiary/asset, amount, start_date, end_date, status (active/expired), notes — plus auto-derived `period_days` and `daily_cost` returned on list/create/update.
- ✅ **Amortization math** (proper accounting): `daily_cost = amount / max(period_days, 1)` where `period_days = (end - start).days + 1` (inclusive). Verified: 1825 SAR over 365 days = exactly 5.00 SAR/day.
- ✅ **CRUD endpoints**: `GET/POST/PUT/DELETE /api/operating-expenses/prepaid[/{id}]`.
- ✅ **Expired/inactive records excluded** from all daily/range calculations and from `summary.prepaid.active_count` and `by_type` aggregation.
- ✅ **Summary endpoint** now returns `prepaid: {total_paid, daily_total, active_count, by_type: {<type>: {total_paid, daily_cost, count}}}`.
- ✅ **Report endpoint** daily/monthly/yearly buckets now include `prepaid_total` and `prepaid_by_type`.
- ✅ **Dashboard integration**: new totals `operating_prepaid_total` and `operating_prepaid_by_type`. The existing `operating_expenses_total` already includes the prepaid sum, so `net_profit` is automatically reduced.
- ✅ **New dashboard KPI card**: `operating_prepaid_total` labeled "المدفوعة مقدماً (تأمين/إقامات)".
- ✅ **Frontend**: dedicated PrepaidPanel + PrepaidFormFields with **live inline preview** (`amount ÷ N يوم = X ر.س / يوم`) so the merchant sees the daily amortization before saving.
- ✅ **4 new pytest tests** (16 total in test_operating_expenses.py): CRUD+math, summary+by_type, dashboard+report integration, expired-status exclusion. **201/201 backend tests pass.**
- ✅ **Testing agent verified**: 100% backend + 100% frontend (5 tabs, CRUD UI, modal preview, table derived fields, summary cards, report sub-rows, dashboard KPI).

## Implemented (2026-05 — Operating Expenses / المصروفات التشغيلية اليومية)
- ✅ **New page `/operating-expenses`** — the formal source of all fixed and variable operating costs used in P&L calculations. Sidebar link "المصروفات التشغيلية" (Wallet icon).
- ✅ **Backend module `expenses_routes.py`** with three independent expense types:
  - **Monthly Salaries** (`operating_salaries` collection) — 3 categories: `employee` (موظفين/إداريين/محاسبين/مسوقين), `household` (مصروف البيت/المنزل/الشخصي), `charity` (الصدقات/التبرعات/الكفالات). Daily cost = `monthly_amount / days_in_month` (calendar-aware).
  - **Annual Rentals** (`operating_rentals` collection) — types: office/warehouse/shop/employee_housing/other. Daily cost = `annual_amount / 365`. Status active/expired (date-bounded).
  - **Daily Variable Expenses** (`operating_daily_expenses` collection) — free-form date+type+description+amount+payment_method.
- ✅ **CRUD endpoints**: `GET/POST/PUT/DELETE /api/operating-expenses/{salaries|rentals|daily}[/{id}]`.
- ✅ **Aggregation endpoints**:
  - `GET /api/operating-expenses/summary` — KPI cards data (per-category monthly totals, today's per-day breakdown, **per-country breakdown**).
  - `GET /api/operating-expenses/report` — daily / monthly / yearly aggregates + custom range.
- ✅ **Stopped/expired records correctly excluded** from all computations.
- ✅ **Dashboard integration**: `GET /api/dashboard` now exposes `operating_expenses_total`, `operating_salaries_total`, `operating_salaries_employee/household/charity`, `operating_rentals_total`, `operating_daily_other_total`. `net_profit` is reduced by `operating_expenses_total`.
- ✅ **Net Sales toggle**: new `net_sales_config.deduct_operating_expenses` flag (default `True`) in Settings → "خصم المصروفات التشغيلية" — controls whether operating expenses are deducted from `net_sales` KPI.
- ✅ **3 new dashboard KPI cards**: `operating_expenses_total`, `operating_salaries_total`, `operating_rentals_total` (in costs group).
- ✅ **Salary editing**: full record edit (name/category/country/amount/start_date/status/notes) via PUT — verified by `test_salary_edit_full_record_persists`.

## Implemented (2026-05 — Salary Country Classification)
- ✅ **New `country` field on salaries** with three values: `saudi` 🇸🇦 / `yemen` 🇾🇪 / `other` 🌍 (default `saudi` for backward compatibility).
- ✅ **Backend validation**: invalid country values rejected with HTTP 400.
- ✅ **Idempotent startup backfill**: pre-existing salaries without `country` get `country=saudi` set automatically.
- ✅ **Summary endpoint** now returns `by_country: {<country>: {monthly_total, count}}` so the dashboard can show per-country totals.
- ✅ **Frontend**: country dropdown (with flag) in salary add/edit modal (`data-testid="oe-salary-country"`), new "الدولة" column in salaries table, and two new summary cards "رواتب السعودية 🇸🇦" and "رواتب اليمن 🇾🇪" at the top of the page.
- ✅ **2 additional pytest tests** covering country persistence, `by_country` aggregation math, invalid-country rejection, and full-record edit persistence. **197/197 backend tests pass.**

## Implemented (2026-05 — Per-Order Date Filtering Across Dashboard)
- ✅ **Excel parser:** picks up the order-creation date column even when the header reads "تاريخ إنشاء الطلب" (now in `DATE_COLS`). Falls back to **column Q (index 16)** when no header matches, matching Salla's standard layout.
- ✅ **Parser bug fix:** `_match_col` no longer false-matches empty header cells (an empty string is no longer treated as a substring of every candidate).
- ✅ **Parser bug fix:** preserves Excel-native date cells (datetime/date) by emitting ISO format instead of `str(datetime)` so the normalizer always succeeds.
- ✅ **Dashboard SSOT shift:** `GET /api/dashboard` now aggregates ALL KPIs (sales, fees, BNPL splits, electronic net, total shipping, deferred shipping, expected Salla transfer, VAT, balances, monthly trend) directly from `unified_orders` filtered by per-order `order_date`, NOT from `analyses.date`. A single upload spanning Jan/Feb/Mar correctly splits across months.
- ✅ **Startup backfill:** any pre-existing unified_orders documents with `order_date_raw` but missing `order_date` get their dates normalized on next backend start (idempotent, logs count).
- ✅ Three new regression tests cover: (1) header label "تاريخ إنشاء الطلب" detection, (2) column-Q fallback when header is unknown, (3) dashboard split-by-order-date when one upload contains multi-month orders. **107/107 backend tests pass.**

## Implemented (2026-05 — Phase 2: Advanced Filters)
- ✅ New shared component `AdvancedFilters.jsx`: date presets (today/yesterday/7d/30d/this-month/last-month/this-year/custom) + payment-methods multi-select + shipping-companies multi-select. Reusable across Dashboard and Reports.
- ✅ Backend `/api/dashboard` and `/api/balances` accept comma-separated `payment_methods` and `shipping_companies` query params and apply case-insensitive partial-match filtering.

## Implemented (2026-05 — Bug fix v2: Auto-inferred date for incoming Make.com orders)
After v1 left 147 orders without date (which annoyed the user since new Make.com webhooks weren't auto-appearing in the dashboard), v2 reintroduces a controlled fallback:
- ✅ When Make.com sends an order WITHOUT `created_at`, the webhook now assigns `order_date = today (UTC)` AND marks `order_date_inferred=True`. Order appears immediately in dashboard.
- ✅ When the SAME order arrives later with a real `created_at`, the merge logic in `orders_db.py` automatically OVERWRITES the inferred date with the authoritative one and flips `order_date_inferred=False`. Excel re-imports work the same way.
- ✅ Startup migration v2: restores `order_date = received_at[:10]` for the 147 orders that v1 had cleared, marking them as inferred. They now reappear in dashboard with a yellow "approximate date" banner.
- ✅ New stat `orders_inferred_date` (count of inferred-date orders) shown on MakeWebhook page in an informational yellow banner: "X طلب بتاريخ تقريبي" + guide to fix Make.com mapping.
- ✅ `orders_missing_date` (truly missing) kept as a separate RED banner (extremely rare now).
- ✅ Webhook response now reports `inferred_date` count.
- ✅ 5 regression tests in `tests/test_no_date_fallback.py` rewritten for new behavior. **126/126 tests pass.**

## Implemented (2026-05 — Bug fix: Make.com orders inflating current month)
- 🐛 **Root cause** (found via Production diagnostic with the user's account): the webhook previously fell back to `datetime.now()` when Make.com sent a payload without `created_at`. This silently labeled March/April orders that Make.com forwarded today as "May orders", inflating the current month's KPIs by ~138 orders for the user.
- ✅ **Fix in `webhook_routes.py`**: removed the today fallback. Orders without `created_at` are now stored with `order_date=None` (still visible on the Make.com page, but excluded from date-filtered dashboard/reports queries).
- ✅ **Startup migration in `server.py`**: detects rows where `data_source=make` + `order_date_raw=''` + `order_date == received_at[:10]` (i.e. previously got the today-fallback) and clears their `order_date` to None. Idempotent; logs `cleared` count.
- ✅ **New endpoint** `GET /api/webhook/orders-missing-date` returns the orders that need attention.
- ✅ **`GET /api/webhook/stats` now exposes** `orders_missing_date` counter; ingest response exposes `without_date`.
- ✅ **UI warning banner** on `/make-webhook`: yellow banner displays count + Make.com fix instructions whenever `orders_missing_date > 0`.
- ✅ **5 new regression tests** in `tests/test_no_date_fallback.py` covering: no-fallback behavior, date-filter exclusion, stats counter, missing-date endpoint, correct-month routing.
- ✅ **126/126 backend tests pass.**

## Implemented (2026-05 — Phase 3: Net Sales Configuration)
- ✅ New Pydantic model `NetSalesConfig` (server.py) with 7 independent flags: `deduct_payment_fees`, `deduct_shipping`, `deduct_deferred_shipping`, `deduct_ads`, `deduct_product_costs`, `deduct_vat`, `deduct_daily_expenses`. Defaults reflect typical Salla seller workflow (deduct payment fees + regular shipping + ads + product costs; don't deduct VAT or deferred shipping).
- ✅ `GET /api/settings` now exposes `net_sales_config`; `PUT /api/settings` accepts and persists it. Backwards compatible (None preserves prior value; missing → defaults applied at read-time).
- ✅ `GET /api/dashboard` computes `totals.net_sales` based on the merchant's config and returns the active `net_sales_config` for the UI to show what's deducted.
- ✅ New Settings page section "حساب صافي المبيعات" with 6 toggles (deduct_daily_expenses kept hidden — folded into product_costs for now) and a live equation preview showing exactly what gets subtracted in real time.
- ✅ New KPI card `net_sales` in `dashboardCards.js` (group "sales") with accent styling and tooltip "حسب إعدادات الخصم". Auto-included in the dashboard-customization toggle list (21 cards now).
- ✅ Backend regression tests `tests/test_net_sales_config.py` (4 tests): default exposure, persistence, dashboard inclusion, custom-flag math verification. **121/121 backend tests pass.**


- ✅ **Single source of truth: `unified_orders` collection.** Both Excel uploads and Make.com webhook write here.
- ✅ New module `orders_db.py` with intelligent merge logic:
  - `_merge_into(existing, incoming, source)` — field-level merge.
  - Empty incoming never overwrites existing; empty existing accepts incoming.
  - Critical fields (`total_amount`, `order_status`, `payment_status`) → newer source wins.
  - Non-critical fields → first writer wins (preserve manual data).
  - `field_sources` dict tags each scalar with its writing source.
  - `data_sources` array records every source touching the order (capped 20 entries).
  - `data_source` field = last writer.
- ✅ **Excel parser extended**: `parse_salla_excel` now returns `orders_individual[]` with full per-row fields (customer_name, customer_mobile, subtotal, shipping_cost, discount, currency, source, status). 6 new column matchers added.
- ✅ **Upload-excel endpoint**: after report generation, upserts every parsed order to `unified_orders` with `data_source="excel"`. Returns `orders_imported` + `orders_updated` counters.
- ✅ **Make webhook** rewritten to use `upsert_order()` with `data_source="make"`. UTM fields (utm_source, utm_medium, utm_campaign, device) now persisted.
- ✅ **Build-analysis** reads from `unified_orders` so analytics aggregate across BOTH sources naturally (no double-counting via order_number dedup).
- ✅ **Stats endpoint** returns `by_source: {excel: N, make: M}` breakdown.
- ✅ **DELETE webhook settings** only deletes Make-sourced orders (preserves Excel rows).
- ✅ **One-time migration** on startup copies legacy `webhook_orders` → `unified_orders` (idempotent).
- ✅ **Frontend**: `MakeWebhook.jsx` renamed table to "آخر الطلبات الموحَّدة (Excel + Make.com)"; per-row colored Make/Excel badges + sky-blue "مدمج" chip when an order has been touched by both sources; new "Make / Excel" KPI card.
- ✅ **Testing**: **80/80 backend tests pass** (9 new tests covering bidirectional merge, field provenance, source isolation, build-analysis cross-source aggregation). Frontend Playwright fully green.

## Implemented (2026-05 — Make.com Webhook Source)
- ✅ **Second data source: Make.com webhook integration.** Salla → Make.com → /api/webhook/make/{token} → same DB → same reports.
- ✅ Backend module `webhook_routes.py`:
  - `POST /api/webhook/make/{token}` (PUBLIC, token-authed) — accepts single object, array, or `{orders: [...]}`. Upsert by `(user_id, order_number)` ensures no duplicates and supports updates.
  - `GET /api/webhook/settings` (JWT) — auto-creates token; returns webhook_url + sample payload.
  - `POST /api/webhook/settings/rotate-token` — invalidates old token immediately.
  - `DELETE /api/webhook/settings` — disconnect: removes token + all stored orders for the user.
  - `GET /api/webhook/orders` — list received orders (date_from/date_to/limit), DESC by order_date.
  - `GET /api/webhook/stats` — total_orders_in_db, total_received_ever, last_sync_at, date_range (earliest/latest).
  - `POST /api/webhook/build-analysis` — aggregates orders in [date_from, date_to] → `analyses` document with `source: "make"`, using the EXACT same `match_settings()` + `_build_report()` pipeline as Excel.
- ✅ MongoDB collections: `webhook_tokens` (unique on user_id + token), `webhook_orders` (unique on (user_id, order_number), index on order_date).
- ✅ `_orders_to_parsed()` bridges raw orders → `parse_salla_excel`-compatible dict, so the rest of the pipeline (dashboard, reports, daily costs, shipping accounts, BNPL fees, KPI cards) works unchanged.
- ✅ Liberal date parsing: handles YYYY-MM-DD, ISO 8601, DD/MM/YYYY, etc.
- ✅ Pydantic `Config.extra="allow"` + full `raw` JSON preserved on each order — no data loss from unknown Make.com mapping fields.
- ✅ Frontend page `/make-webhook` (`MakeWebhook.jsx`): copyable webhook URL, token rotate/disconnect, sample JSON payload, build-analysis form (date range + ads/products costs), recent-orders table, stats KPIs (stored/received/last sync/date range).
- ✅ Sidebar: new `ربط Make.com` link.
- ✅ Testing: **71/71 backend tests pass** (16 new webhook tests + 55 prior). Frontend Playwright fully green.

## Implemented (2026-05 — Deferred Shipping Companies)
- ✅ **Two-tier shipping**: each shipping company can be marked `is_deferred=true` in Settings.
  - Regular companies: cost deducted directly from sales (default behavior).
  - Deferred companies: cost still counted as expense (net_profit), but **not** deducted from the projected Salla→bank transfer.
- ✅ **New backend module** `shipping_accounts.py`:
  - `GET /api/shipping-accounts` — list each deferred company with total_owed (from analyses) + total_paid (from ledger) + remaining.
  - `GET /api/shipping-accounts/{company}/payments` — payment history (DESC by date).
  - `POST /api/shipping-accounts/{company}/payments` — record a payment {amount, payment_date, invoice_number, note}.
  - `DELETE /api/shipping-accounts/payments/{payment_id}` — undo a payment.
  - MongoDB collection `shipping_payments` with index `(user_id, company_name, payment_date desc)`.
- ✅ **Excel parser & report**: `match_settings()` now propagates `is_deferred` into each `shipping_breakdown` row and computes `deferred_shipping_cost` aggregate.
- ✅ **Dashboard**: new fields/KPIs `deferred_shipping_cost`, `regular_shipping_cost`, and `expected_salla_transfer = total_sales − total_payment_fees − regular_shipping_cost`.
- ✅ **Frontend page** `/shipping-accounts` (`ShippingAccounts.jsx`): summary KPIs (Owed/Paid/Remaining), per-company cards with progress bar, expandable payment ledger (with delete), and "add payment" modal.
- ✅ **Settings UI**: 14-column grid with a dedicated "آجل" checkbox per shipping row.
- ✅ **Sidebar**: new `حسابات الشحن الآجلة` link.
- ✅ Testing: **55/55 backend tests pass** (13 new shipping-accounts tests, 19 snapchat, 23 base). Frontend Playwright smoke green.

## Implemented (2026-02)
- ✅ JWT custom auth (register/login/logout/me) with httpOnly cookies + bearer token.
- ✅ Sidebar layout (RTL) — 6 صفحات: Dashboard, Upload, History, Daily Costs, Reports, Settings.
- ✅ Excel parser (auto-detects Arabic/English column names from Salla).
- ✅ Settings: edit payment commissions + shipping costs per company + VAT.
- ✅ Analysis creation with file upload + costs + matched commissions.
- ✅ Analysis result page: KPIs + Pie chart (payments) + Bar chart (shipping) + tables.
- ✅ Daily costs page: add/edit/delete by date (incl. Snapchat ×2, TikTok, Instagram, Google, مصاريف يومية).
- ✅ Reports page: aggregated across all analyses with charts and tables.
- ✅ History page with search & deletion.
- ✅ PDF and Excel export endpoints + frontend buttons.
- ✅ Dashboard with monthly trend (LineChart) and recent analyses + date range filter.
- ✅ BNPL (Tamara/Tabby) fees separated into a distinct KPI card.

## Implemented (2026-05 — Snapchat Integration)
- ✅ **Snapchat Marketing API OAuth integration** — per-user account connect:
  - Backend module `snapchat_routes.py` (separate from `server.py`):
    - `POST /api/snapchat/config` — save client_id / client_secret / redirect_uri (upsert)
    - `GET  /api/snapchat/config` — return status (without leaking client_secret)
    - `DELETE /api/snapchat/config` — disconnect
    - `GET  /api/snapchat/authorize-url` — build Snapchat OAuth URL with signed JWT state
    - `GET  /api/snapchat/oauth/callback` — handle code → exchange → store refresh_token; always redirects to `/settings?snapchat=success|error`
    - `GET  /api/snapchat/adaccounts` — list ad accounts (auto-refresh access_token)
    - `POST /api/snapchat/select-adaccount` — persist selected ad account
    - `GET  /api/snapchat/daily-spend?date=YYYY-MM-DD` — fetch daily spend (handles micro-currency conversion ÷1,000,000)
  - MongoDB: `snapchat_connections` collection, unique index on `user_id`.
  - State CSRF defense: signed JWT (10 min TTL) embedding user_id — no cookies/headers needed on callback.
  - Frontend Settings page: dedicated "ربط Snapchat Ads" card with App ID / App Secret (password) / Redirect URI fields + Connect/Disconnect/Re-connect + ad account picker.
  - Frontend Daily Costs page: small "Snap" button next to سناب شات input → calls `/snapchat/daily-spend` for selected date and auto-fills the field.
- ✅ **Bug fix — logout cookie deletion**: `clear_auth_cookies` now mirrors `set_auth_cookies` attributes (Secure, SameSite=None, HttpOnly, Path=/). Previously the deletion Set-Cookie had `SameSite=lax` → browsers ignored it → logout was a no-op. Verified end-to-end with a real browser via Playwright.
- ✅ **Bug fix — Recharts width(-1) warnings**: ResponsiveContainer now uses `width="99%" minWidth={0} minHeight={0}` across Dashboard / Reports / AnalysisResult.

## Backlog / Next
### ⏸️ Awaiting merchant approval
- **Meta OAuth Flow — خيار B ("اربط مع Facebook")**: one-click Meta login that eliminates manual token copying entirely. Mirrors the Snapchat OAuth flow (Configure App ID + Secret + Redirect URI → click "اربط" → Meta consent screen → callback persists 60-day token + permissions). Will reuse the existing `meta_connections` schema and add `redirect_uri` + signed-JWT state. **NOT to be started until merchant explicitly approves.**

### P1
- Snapchat campaign creation (P1 from user; user explicitly deferred this).
- TikTok / Instagram Ads API direct integration (mirror Snapchat flow).
- Auto-fill Daily Costs for all platforms by date (one-click fetch).
- Multi-user team workspaces / sharing.
- Forgot password + email-based reset (currently console-logged).
- Currency localization (currently SAR hard-coded).

### P2
- Compare two analyses side-by-side.
- Profit per product analysis (requires line-items in Excel).
- Server-side refresh token revocation/blacklist (defense-in-depth on logout).

## Test Credentials
See `/app/memory/test_credentials.md`.
