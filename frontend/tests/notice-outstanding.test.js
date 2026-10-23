import { beforeEach, expect, it, vi } from "vitest";
const request = vi.hoisted(() => vi.fn());
vi.mock("../src/api/client", () => ({ request }));
import { listOutstanding } from "../src/api/notices";
beforeEach(() => {
  request.mockReset();
});
it("전체 페이지의 점포 미확인 수를 확인한 후 공지 필터와 페이지 수를 계산한다", async () => {
  const notices = Array.from({ length: 7 }, (_, i) => ({ notice_id: `n${i}` }));
  request.mockImplementation(async ({ url, params }) => {
    if (url === "/notices")
      return {
        data: params.page === 1 ? notices.slice(0, 3) : notices.slice(3),
        meta: { total_pages: 2 },
      };
    const i = Number(url.match(/n(\d+)/)[1]);
    return {
      summary: {
        target_count: i === 0 ? 0 : 12,
        unacknowledged_count: [0, 0, 1, 12, 0, 3, 0][i],
      },
    };
  });
  const response = await listOutstanding({ page: 2, page_size: 2 });
  expect(response.data.map((row) => row.notice_id)).toEqual(["n5"]);
  expect(response.meta).toEqual({
    page: 2,
    page_size: 2,
    total: 3,
    total_pages: 2,
  });
  expect(
    request.mock.calls.filter(([args]) => args.url !== "/notices"),
  ).toHaveLength(7);
  expect(request.mock.calls[0][0].params).not.toHaveProperty("unread");
});
it("확인 현황 오류를 모든 점포 확인 완료로 처리하지 않는다", async () => {
  request.mockImplementation(async ({ url }) => {
    if (url === "/notices")
      return { data: [{ notice_id: "n1" }], meta: { total_pages: 1 } };
    throw new Error("조회 실패");
  });
  await expect(listOutstanding()).rejects.toThrow("조회 실패");
});
it("확인 현황 집계가 누락되면 빈 목록으로 오인시키지 않는다", async () => {
  request.mockImplementation(async ({ url }) =>
    url === "/notices"
      ? { data: [{ notice_id: "n1" }], meta: { total_pages: 1 } }
      : { summary: {} },
  );
  await expect(listOutstanding()).rejects.toThrow("점포 확인 현황");
});
