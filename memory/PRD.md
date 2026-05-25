# PRD — Hesab (تطبيق محاسبي ذكي لمنصة سلة)

## Original Problem Statement
أريد بناء تطبيق محاسبي ذكي للتجارة الإلكترونية يقوم بتحليل ملفات Excel المصدرة من منصة سلة واستخراج وتحليل البيانات المالية تلقائيًا.

## Architecture
- **Backend**: FastAPI + Motor (MongoDB async) — JWT auth (cookies + bearer), openpyxl Excel parsing, xlsxwriter Excel export, reportlab + arabic-reshaper for PDF export.
- **Frontend**: React 19 + React Router 7 + TailwindCSS + Shadcn/UI + Recharts + @phosphor-icons/react.
- **Database**: MongoDB collections: `users`, `settings`, `daily_costs`, `analyses`.

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

## Implemented (2026-02)
- ✅ JWT custom auth (register/login/logout/me) with httpOnly cookies + bearer token.
- ✅ Sidebar layout (RTL) مع 6 صفحات: Dashboard, Upload, History, Daily Costs, Reports, Settings.
- ✅ Excel parser (auto-detects Arabic/English column names from Salla).
- ✅ Settings: edit payment commissions + shipping costs per company.
- ✅ Analysis creation with file upload + costs + matched commissions.
- ✅ Analysis result page: KPIs + Pie chart (payments) + Bar chart (shipping) + tables.
- ✅ Daily costs page: add/edit/delete by date.
- ✅ Reports page: aggregated across all analyses with charts and tables.
- ✅ History page with search & deletion.
- ✅ PDF and Excel export endpoints + frontend buttons.
- ✅ Dashboard with monthly trend (LineChart) and recent analyses.

## Backlog / Next
### P0
- Validate end-to-end Excel upload flow with sample file.

### P1
- Multi-user team workspaces / sharing.
- Forgot password + email-based reset (currently console-logged).
- Currency localization (currently SAR hard-coded).

### P2
- Compare two analyses side-by-side.
- Profit per product analysis (requires line-items in Excel).
- Mobile responsive sidebar (currently hidden on small screens).
- Snapchat/TikTok/Instagram API direct integration for ad-spend auto-fetch.

## Test Credentials
See `/app/memory/test_credentials.md`.
