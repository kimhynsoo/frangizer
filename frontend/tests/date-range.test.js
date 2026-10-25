import { expect, it } from "vitest";
import { dateRangeQuery, koreaToday } from "../src/date-range";
const now = new Date("2026-10-24T11:00:00Z");
it("양끝 날짜를 포함하고 종료 다음날 0시는 제외한다", () => {
  expect(dateRangeQuery("2026-10-19", "2026-10-23", now)).toEqual({
    from: "2026-10-18T15:00:00.000Z",
    to: "2026-10-23T15:00:00.000Z",
  });
});
it("오늘은 미래 시각을 보내지 않고 현재까지 조회한다", () => {
  expect(dateRangeQuery("2026-10-24", "2026-10-24", now).to).toBe(
    now.toISOString(),
  );
});
it("정확히 31일은 허용한다", () => {
  expect(dateRangeQuery("2026-09-23", "2026-10-23", now)).toBeTruthy();
});
it.each([
  ["", "2026-10-23"],
  ["2026-10-23", ""],
  ["2026-10-23", "2026-10-19"],
  ["2026-10-19", "2026-10-25"],
  ["2026-09-22", "2026-10-23"],
])("잘못된 기간 %s ~ %s 거부", (start, end) => {
  expect(() => dateRangeQuery(start, end, now)).toThrow();
});
it("한국 시간 기준 오늘을 사용한다", () => {
  expect(koreaToday(new Date("2026-10-24T15:00:00Z"))).toBe("2026-10-25");
});
