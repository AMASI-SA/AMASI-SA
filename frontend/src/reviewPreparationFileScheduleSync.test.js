import { riyadhLocalDateTimeToIso } from "./reviewPreparationFileMetadataEnhancer";
import { preparationFileCreatedSchedule } from "./reviewPreparationFileScheduleSync";


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
