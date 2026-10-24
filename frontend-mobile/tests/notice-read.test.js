// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
const detail = vi.hoisted(() => vi.fn());
vi.mock("../src/api/documents", () => ({ detail, download: vi.fn() }));
import SourcePanel from "../src/components/SourcePanel.vue";
let wrapper;
const fixture = (
  page = 1,
  total = 1,
  sections = [
    {
      chunk_id: "one",
      text: "문서 본문",
      effective_from: "2026-01-01T00:00:00+09:00",
    },
  ],
) => ({
  data: {
    version: {
      version: "1",
      is_demo: true,
      material_type: "synthetic",
      effective_from: "2026-01-01T00:00:00+09:00",
      original_source: {},
    },
    sections,
    sections_meta: { page, page_size: 10, total: 11, total_pages: total },
  },
});
const confirmButton = () =>
  wrapper.findAll("button").find((b) => b.text() === "확인 완료");
async function open(props = {}) {
  wrapper = mount(SourcePanel, {
    props: {
      source: {
        notice_id: "notice",
        document_id: "doc",
        version_id: "version",
      },
      requireRead: true,
      ...props,
    },
    global: { directives: { "focus-trap": {} } },
  });
  await flushPromises();
  return wrapper;
}
async function scrollTo(top) {
  const panel = wrapper.get(".source-panel");
  Object.defineProperty(panel.element, "clientHeight", {
    value: 500,
    configurable: true,
  });
  Object.defineProperty(panel.element, "scrollHeight", {
    value: 1500,
    configurable: true,
  });
  panel.element.getBoundingClientRect = () => ({
    top: 0,
    bottom: 500,
    height: 500,
  });
  const end = wrapper.find(".source-read-end");
  if (end.exists())
    end.element.getBoundingClientRect = () => ({
      top: 1000 - panel.element.scrollTop,
      bottom: 1000 - panel.element.scrollTop,
      height: 1,
    });
  panel.element.scrollTop = top;
  await panel.trigger("scroll");
}
beforeEach(() => {
  detail.mockReset().mockResolvedValue(fixture());
});
afterEach(() => wrapper?.unmount());
it("문서 중간에서는 확인 불가, 끝에서만 확인 가능", async () => {
  await open();
  expect(confirmButton().attributes("disabled")).toBeDefined();
  await scrollTo(400);
  expect(wrapper.emitted("readComplete")).toBeUndefined();
  expect(confirmButton().attributes("disabled")).toBeDefined();
  await scrollTo(1000);
  expect(confirmButton().attributes("disabled")).toBeUndefined();
  expect(wrapper.emitted("readComplete")[0][0].notice_id).toBe("notice");
  await confirmButton().trigger("click");
  expect(wrapper.emitted("confirm")).toHaveLength(1);
});
it("마지막 페이지만 읽어서는 확인되지 않고 모든 페이지의 끝을 확인해야 한다", async () => {
  detail.mockImplementation(async (_doc, _version, { page }) =>
    fixture(page, 2),
  );
  await open();
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "다음")
    .trigger("click");
  await flushPromises();
  await scrollTo(1000);
  expect(confirmButton().attributes("disabled")).toBeDefined();
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "이전")
    .trigger("click");
  await flushPromises();
  expect(confirmButton().attributes("disabled")).toBeDefined();
  await scrollTo(1000);
  expect(confirmButton().attributes("disabled")).toBeUndefined();
});
it("본문이 비어 있거나 조회 실패 시 스크롤해도 확인하지 않는다", async () => {
  detail.mockResolvedValue(fixture(1, 0, []));
  await open();
  await scrollTo(1000);
  expect(wrapper.emitted("readComplete")).toBeUndefined();
  expect(confirmButton().attributes("disabled")).toBeDefined();
  detail.mockRejectedValue(new Error("문서 조회 실패"));
  await wrapper.setProps({
    source: { notice_id: "other", document_id: "other", version_id: "other" },
  });
  await flushPromises();
  await scrollTo(1000);
  expect(wrapper.emitted("readComplete")).toBeUndefined();
});
it("다른 문서로 바꾸면 확인 상태가 초기화된다", async () => {
  await open();
  await scrollTo(1000);
  expect(confirmButton().attributes("disabled")).toBeUndefined();
  await wrapper.setProps({
    source: { notice_id: "other", document_id: "other", version_id: "other" },
  });
  await flushPromises();
  expect(confirmButton().attributes("disabled")).toBeDefined();
});

it("본문 끝을 읽으면 아래 출처·다운로드 영역까지 반복해서 내리지 않아도 읽음으로 기록한다", async () => {
  await open();
  await scrollTo(400);
  expect(confirmButton().attributes("disabled")).toBeDefined();
  await scrollTo(500);
  // 본문 끝은 보이지만 전체 패널 끝(scrollTop 1000)은 아직 아니다.
  expect(confirmButton().attributes("disabled")).toBeUndefined();
});
it("읽지 않은 페이지를 표시하고 바로 이동할 수 있다", async () => {
  detail.mockImplementation(async (_doc, _version, { page }) =>
    fixture(page, 2),
  );
  await open();
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "다음")
    .trigger("click");
  await flushPromises();
  await scrollTo(1000);
  expect(wrapper.get(".unread-page-list").text()).toBe("미확인 페이지 · 1");
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "미확인 페이지로 이동")
    .trigger("click");
  await flushPromises();
  expect(detail.mock.calls.at(-1)[2].page).toBe(1);
  expect(confirmButton().attributes("disabled")).toBeDefined();
  await scrollTo(1000);
  expect(confirmButton().attributes("disabled")).toBeUndefined();
});
