import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import PublicLegalApp, {
    isPublicLegalPath,
    normalizeLegalPath,
} from "./PublicLegalApp";

describe("public legal pages", () => {
    test("recognizes only the three public legal paths", () => {
        expect(isPublicLegalPath("/privacy-policy")).toBe(true);
        expect(isPublicLegalPath("/privacy-policy/")).toBe(true);
        expect(isPublicLegalPath("/data-deletion?source=meta")).toBe(true);
        expect(isPublicLegalPath("/terms#top")).toBe(true);
        expect(isPublicLegalPath("/login")).toBe(false);
        expect(normalizeLegalPath("/terms/")).toBe("/terms");
    });

    test("privacy policy is public, bilingual, and identifies the controller", () => {
        const markup = renderToStaticMarkup(
            <PublicLegalApp path="/privacy-policy" />,
        );
        expect(markup).toContain("سياسة الخصوصية");
        expect(markup).toContain("Privacy Policy");
        expect(markup).toContain("مؤسسة أماسي الخليج التجارية");
        expect(markup).toContain("support@amasi-sa.com");
        expect(markup).toContain("30 يومًا");
        expect(markup).toContain("Snapchat Conversions API");
        expect(markup).toContain("SHA-256");
        expect(markup).toContain("لا يرسل ميزان عنوان IP أو User-Agent");
        expect(markup).not.toContain("تسجيل الدخول مطلوب");
    });

    test("data deletion page gives actionable Meta deletion instructions", () => {
        const markup = renderToStaticMarkup(
            <PublicLegalApp path="/data-deletion" />,
        );
        expect(markup).toContain("تعليمات حذف بيانات المستخدم");
        expect(markup).toContain("User Data Deletion Instructions");
        expect(markup).toContain("رمز وصول Meta المشفر");
        expect(markup).toContain("لا ترسل كلمة المرور");
        expect(markup).toContain("support@amasi-sa.com");
    });

    test("terms page states authorized use and Saudi governing law", () => {
        const markup = renderToStaticMarkup(
            <PublicLegalApp path="/terms" />,
        );
        expect(markup).toContain("شروط الاستخدام");
        expect(markup).toContain("Terms of Use");
        expect(markup).toContain("أنظمة المملكة العربية السعودية");
        expect(markup).toContain("الوصول إلى حسابات أو بيانات دون تفويض");
    });
});
