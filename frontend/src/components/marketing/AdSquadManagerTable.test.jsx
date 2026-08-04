import {
  readAdSquadSortPreference,
  sortAdSquadRows,
} from "./AdSquadManagerTable";

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }
}

const rows = [
  {
    ad_squad_id: "old-active",
    ad_squad_name: "قديمة نشطة",
    status: "ACTIVE",
    spend_sar: 20,
    start_time: "2026-08-01T10:00:00Z",
  },
  {
    ad_squad_id: "new-paused",
    ad_squad_name: "حديثة متوقفة",
    status: "PAUSED",
    spend_sar: 100,
    start_time: "2026-08-04T10:00:00Z",
  },
  {
    ad_squad_id: "newer-active",
    ad_squad_name: "الأحدث نشطة",
    status: "ACTIVE",
    spend_sar: 50,
    start_time: "2026-08-05T10:00:00Z",
  },
];

describe("Ad Squad display sorting", () => {
  test("defaults to newest and handles invalid stored values safely", () => {
    const storage = new MemoryStorage();
    expect(readAdSquadSortPreference(storage)).toBe("newest");
    storage.setItem("mezan-snapchat-adsquad-sort-v1", "unsupported");
    expect(readAdSquadSortPreference(storage)).toBe("newest");
  });

  test("sorts newest first by default", () => {
    expect(sortAdSquadRows(rows, "newest").map((row) => row.ad_squad_id))
      .toEqual(["newer-active", "new-paused", "old-active"]);
  });

  test("sorts by spend or active state when selected", () => {
    expect(sortAdSquadRows(rows, "spend").map((row) => row.ad_squad_id))
      .toEqual(["new-paused", "newer-active", "old-active"]);
    expect(sortAdSquadRows(rows, "active").map((row) => row.ad_squad_id))
      .toEqual(["newer-active", "old-active", "new-paused"]);
  });
});
