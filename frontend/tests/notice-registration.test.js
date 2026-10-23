// @vitest-environment happy-dom
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
const m = vi.hoisted(() => ({
  list: vi.fn(),
  versions: vi.fn(),
  publish: vi.fn(),
}));
vi.mock("../src/api/documents", () => m);
import NoticeRegistration from "../src/components/NoticeRegistration.vue";
let wrapper;
const version = {
  document_id: "doc",
  version_id: "approved",
  version: "1.1",
  title: "운영 기준",
  lifecycle: "approved",
  index_status: "ready",
  effective_from: "2026-10-26T00:00:00+09:00",
  visibility: "owner",
  change_summary: "계량 기준 변경",
};
beforeEach(() => {
  vi.clearAllMocks();
  m.list.mockResolvedValue({
    data: [{ document_id: "doc", title: "운영 기준" }],
    meta: { total_pages: 1 },
  });
  m.versions.mockResolvedValue({
    data: [
      version,
      { ...version, version_id: "draft", lifecycle: "draft" },
      { ...version, version_id: "failed", index_status: "failed" },
      { ...version, version_id: "published", lifecycle: "published" },
    ],
    meta: { total_pages: 1 },
  });
  m.publish.mockResolvedValue({ data: { notice_id: "new-notice" } });
});
afterEach(() => wrapper?.unmount());
async function open() {
  wrapper = mount(NoticeRegistration, {
    global: { directives: { "focus-trap": {} } },
  });
  await flushPromises();
  await wrapper.findAll("select")[0].setValue("doc");
  await flushPromises();
}
it("승인·검색 완료 판본만 선택하고 제목을 입력해 기존 배포 API로 등록한다", async () => {
  await open();
  expect(
    wrapper
      .findAll("select")[1]
      .findAll("option")
      .map((o) => o.attributes("value")),
  ).toEqual(["", "approved"]);
  await wrapper.findAll("select")[1].setValue("approved");
  expect(wrapper.get("input").element.value).toBe("운영 기준 v1.1 적용 안내");
  expect(wrapper.text()).toContain("계량 기준 변경");
  await wrapper.get("input").setValue("새 운영 기준 적용 공지");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(m.publish).toHaveBeenCalledWith(
    "doc",
    "approved",
    "새 운영 기준 적용 공지",
    expect.any(String),
  );
  expect(wrapper.emitted("registered")[0][0].notice_id).toBe("new-notice");
});
it("배포 가능한 판본이 없으면 등록 API를 호출하지 않는다", async () => {
  m.versions.mockResolvedValue({ data: [], meta: { total_pages: 0 } });
  await open();
  expect(wrapper.text()).toContain("배포 가능한 판본이 없습니다");
  await wrapper.get("form").trigger("submit");
  expect(m.publish).not.toHaveBeenCalled();
});
it("등록 실패 후 같은 내용 재시도는 같은 멱등키를 사용한다", async () => {
  await open();
  await wrapper.findAll("select")[1].setValue("approved");
  m.publish.mockRejectedValueOnce({ message: "연결 실패", retryable: true });
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(wrapper.text()).toContain("연결 실패");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(m.publish).toHaveBeenCalledTimes(2);
  expect(m.publish.mock.calls[0][3]).toBe(m.publish.mock.calls[1][3]);
});
