import { riyadhLocalDateTimeToIso } from "./reviewPreparationFileMetadataEnhancer";
import { preparationFileCreatedSchedule, persistPreparationFileSchedule } from "./reviewPreparationFileScheduleSync";
import { updatePreparationFileSchedule } from "./services/preparationWorkService";

jest.mock("./services/preparationWorkService", () => ({ updatePreparationFileSchedule: jest.fn() }));


test("mandatory local Riyadh deadline is converted to UTC safely", () => {
  expect(riyadhLocalDateTimeToIso("2026-08-05T14:30")).toBe(
    "2026-08-05T11:30:00.000Z",
  );
  expect(riyadhLocalDateTimeToIso("invalid")).toBeNull();
});


test("created file receives automatic schedule payload", () => {
  expect(preparationFileCreatedSchedule(
    { file_number: "PF-20260803-0012" },
    { mode: "automatic", requiredDueAt: null },
  )).toEqual({
    fileNumber: "PF-20260803-0012",
    mode: "automatic",
    requiredDueAt: null,
  });
});


test("required schedule is rejected without a deadline", () => {
  expect(preparationFileCreatedSchedule(
    { file_number: "PF-20260803-0012" },
    { mode: "required", requiredDueAt: null },
  )).toBeNull();
});

test("verified atomic assignment keeps its required schedule without a second scheduling write", () => {
  expect(preparationFileCreatedSchedule({ file_number: "PF-ATOMIC", assignment_contract: "preparation_assignment_v2",
    schedule_committed: true, schedule_mode: "required", required_due_at: "2026-12-01T10:00:00.000Z" }, null)).toBeNull();
});

test.each([
  { assignment_contract: "preparation_assignment_v2" },
  { schedule_committed: true },
  { assignment_contract: "unknown", schedule_committed: true },
  { assignment_contract: "preparation_assignment_v2", schedule_committed: "true" },
])("ordinary or unverified events retain the existing schedule behavior: %j", (flags) => {
  expect(preparationFileCreatedSchedule({ file_number: "PF-ORDINARY", ...flags },
    { mode: "required", requiredDueAt: "2026-12-01T10:00:00.000Z" })).toEqual({
    fileNumber: "PF-ORDINARY", mode: "required", requiredDueAt: "2026-12-01T10:00:00.000Z",
  });
});

test("the real modern event handler cannot consume a newer ordinary pending required deadline", async () => {
  updatePreparationFileSchedule.mockReset().mockResolvedValue({});
  const pending = { mode: "required", requiredDueAt: "2026-12-02T10:00:00.000Z" };
  window.__mezanPreparationFileSchedulePending = pending;
  try {
    await persistPreparationFileSchedule({ detail: { file_number: "PF-OLDER-BUNDLE",
      assignment_contract: "preparation_assignment_v2", schedule_committed: true } });
    expect(updatePreparationFileSchedule).not.toHaveBeenCalled();
    expect(window.__mezanPreparationFileSchedulePending).toBe(pending);
    await persistPreparationFileSchedule({ detail: { file_number: "PF-NEWER-ORDINARY" } });
    expect(updatePreparationFileSchedule).toHaveBeenCalledTimes(1);
    expect(updatePreparationFileSchedule).toHaveBeenCalledWith("PF-NEWER-ORDINARY", pending);
    expect(window.__mezanPreparationFileSchedulePending).toBeUndefined();
  } finally { delete window.__mezanPreparationFileSchedulePending; }
});
