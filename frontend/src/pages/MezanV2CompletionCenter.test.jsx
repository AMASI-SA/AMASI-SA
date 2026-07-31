import { renderToStaticMarkup } from "react-dom/server";
import { MezanV2CompletionCenterView } from "./MezanV2CompletionCenter";
import {
    LEGACY_MIGRATION_GROUPS,
    MEZAN_V2_WORKSTREAMS,
    PARALLEL_WORKSTREAMS,
    STATUS_META,
    getCompletionSummary,
} from "../data/mezanV2CompletionPlan";

test("completion registry has unique tasks and only known statuses", () => {
    const tasks = MEZAN_V2_WORKSTREAMS.flatMap((workstream) => workstream.tasks);
    const ids = tasks.map((task) => task.id);

    expect(new Set(ids).size).toBe(ids.length);
    tasks.forEach((task) => {
        expect(STATUS_META[task.status]).toBeDefined();
        expect(task.title).toBeTruthy();
    });
});

test("core percentage counts verified completions but excludes future deferred work", () => {
    const summary = getCompletionSummary();
    const coreTasks = MEZAN_V2_WORKSTREAMS
        .filter((workstream) => workstream.core)
        .flatMap((workstream) => workstream.tasks)
        .filter((task) => task.status !== "deferred");
    const completed = coreTasks.filter((task) => task.status === "completed").length;

    expect(summary.total).toBe(coreTasks.length);
    expect(summary.completed).toBe(completed);
    expect(summary.percent).toBe(Math.round((completed / coreTasks.length) * 100));
    expect(summary.pending).toBeGreaterThan(0);
    expect(summary.waiting).toBeGreaterThan(0);
});

test("migration register has one destination and retirement gate per legacy group", () => {
    const ids = LEGACY_MIGRATION_GROUPS.map((item) => item.id);

    expect(new Set(ids).size).toBe(ids.length);
    expect(LEGACY_MIGRATION_GROUPS.some((item) => item.decision === "redirected")).toBe(true);
    expect(LEGACY_MIGRATION_GROUPS.some((item) => item.decision === "keep_now")).toBe(true);
    LEGACY_MIGRATION_GROUPS.forEach((item) => {
        expect(item.destination).toBeTruthy();
        expect(item.retireWhen).toBeTruthy();
    });
});

test("parallel workstreams use unique isolated branch names", () => {
    const branches = PARALLEL_WORKSTREAMS.map((item) => item.branch);

    expect(new Set(branches).size).toBe(branches.length);
    expect(PARALLEL_WORKSTREAMS.filter((item) => item.canStart).length).toBeGreaterThanOrEqual(4);
    branches.forEach((branch) => expect(branch).toMatch(/^agent\//));
});

test("page renders the official core roadmap and next recommended step", () => {
    const markup = renderToStaticMarkup(<MezanV2CompletionCenterView />);
    const coreWorkstreams = MEZAN_V2_WORKSTREAMS.filter((workstream) => workstream.core);

    expect(markup).toContain("خطة اكتمال ميزان 2");
    expect(markup).toContain("الخطوة التالية المعتمدة");
    expect(markup).toContain("قيد التنفيذ");
    expect(markup).toContain('data-testid="completion-tab-migration"');
    expect(markup).toContain('data-testid="completion-tab-parallel"');
    coreWorkstreams.forEach((workstream) => {
        expect(markup).toContain(`data-testid="completion-workstream-${workstream.id}"`);
    });
});
