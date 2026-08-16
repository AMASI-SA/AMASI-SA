import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "CampaignRecommendations.jsx"), "utf8");
const dashboard = fs.readFileSync(path.join(__dirname, "AdvancedDashboard.jsx"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

test("all recommendations open in their own protected page", () => {
    expect(dashboard).toContain('to="/ads-manager/recommendations"');
    expect(dashboard).not.toContain("عرض جميع التوصيات في مساعد ميزان");
    expect(app).toContain('path="/ads-manager/recommendations"');
    expect(app).toContain("<Layout><CampaignRecommendations /></Layout>");
});

test("recommendation page explains the decision and observation window", () => {
    expect(page).toContain('api.get("/ads-manager/ai-monitor/latest")');
    expect(page).toContain("لماذا اتُخذ هذا القرار؟");
    expect(page).toContain("الأرقام التي بنى عليها القرار");
    expect(page).toContain("كم ساعة أصبر؟");
    expect(page).toContain("متى نعتبر القرار ناجحًا؟");
    expect(page).toContain("ماذا لو تجاهلنا التوصية؟");
    expect(page).toContain("أثر الحملة على الربح");
    expect(page).toContain("نسبتها من صافي النتيجة");
    expect(page).toContain("توقع فترة الانتظار القادمة");
    expect(page).toContain("فترة الدراسة:");
    expect(page).toContain("زادت صافي الخسارة");
    expect(page).toContain("خفضت صافي الربح");
    expect(page).toContain("زيادة متوقعة في الربح");
    expect(page).toContain("forecast_without_action_sar");
    expect(page).toContain("forecast_with_action_sar");
    expect(page).toContain("forecast_delta_sar");
    expect(page).toContain("ترتيب تلقائي: الأهم والأكثر تأثيرًا أولًا");
    expect(page).toContain("الأعلى أولوية وتأثيرًا");
    expect(page).toContain("أُنشئت");
    expect(page).toContain("item.generated_at || snapshot?.generated_at");
    expect(page).toContain("الحساب الإعلاني: {item.account_name}");
    expect(page).toContain("recommended_wait_hours");
    expect(page).not.toContain("آخر تحديث");
});

test("execution still requires explicit owner confirmation", () => {
    expect(page).toContain("window.confirm");
    expect(page).toContain("موافقة وتنفيذ");
    expect(page).toContain("snapshot_id: snapshot.snapshot_id");
});
