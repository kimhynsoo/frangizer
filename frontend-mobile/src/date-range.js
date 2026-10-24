const DAY = 86400000;
export function koreaToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type) => parts.find((part) => part.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}
export function daysBefore(day, count) {
  return new Date(new Date(`${day}T00:00:00Z`).getTime() - count * DAY)
    .toISOString()
    .slice(0, 10);
}
export function dateRangeQuery(start, end, now = new Date()) {
  if (!start || !end) throw new Error("시작일과 종료일을 모두 선택해 주세요");
  const from = new Date(`${start}T00:00:00+09:00`);
  const last = new Date(`${end}T00:00:00+09:00`);
  if (!Number.isFinite(+from) || !Number.isFinite(+last) || start > end)
    throw new Error("종료일은 시작일과 같거나 이후여야 합니다.");
  if (end > koreaToday(now))
    throw new Error("오늘까지의 날짜만 조회할 수 있습니다.");
  if (+last - +from + DAY > 31 * DAY)
    throw new Error("조회 기간은 두 날짜를 포함해 최대 31일입니다.");
  const to = new Date(Math.min(+last + DAY, +now));
  if (+to <= +from) throw new Error("아직 조회할 수 없는 기간입니다.");
  return { from: from.toISOString(), to: to.toISOString() };
}
