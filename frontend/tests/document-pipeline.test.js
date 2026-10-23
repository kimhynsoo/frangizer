// @vitest-environment happy-dom
import { expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { pagePolicy, indexJobFailure } from "../src/api/documents";
import ErrorNotice from "../src/components/ErrorNotice.vue";
const { detail, list, upload } = vi.hoisted(() => ({
  detail: vi.fn(),
  list: vi.fn(),
  upload: vi.fn(),
}));
vi.mock("../src/stores/auth", () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock("../src/api/documents", async (original) => ({
  ...(await original()),
  detail,
  list,
  upload,
}));
import SourcePanel from "../src/components/SourcePanel.vue";
it("포함 페이지 생략은 전체를 의미하고 중복은 제거한다", () => {
  expect(pagePolicy("", "1, 2, 2")).toEqual({ exclude_pages: [1, 2] });
  expect(pagePolicy("3, 4, 3", "1")).toEqual({
    include_pages: [3, 4],
    exclude_pages: [1],
  });
});
it("포함·제외 충돌과 잘못된 쪽수는 서버로 보내기 전에 거부한다", () => {
  expect(() => pagePolicy("3,4", "4")).toThrow("동시에");
  for (const value of ["0", "-1", "1.5", "1e2", "2,", "9007199254740992"])
    expect(() => pagePolicy(value)).toThrow();
});
it("색인 실패의 추적 정보는 유지하고 미확정 코드도 임의로 재시도하지 않는다", () => {
  const error = indexJobFailure({
    job_id: "job-1",
    error_code: "NEW_SERVER_CODE",
    error_message: "서버가 제공한 원인",
  });
  const wrapper = mount(ErrorNotice, { props: { error } });
  expect(wrapper.text()).toContain("NEW_SERVER_CODE");
  expect(wrapper.text()).toContain("job-1");
  expect(wrapper.text()).toContain("서버가 제공한 원인");
  expect(wrapper.find("button").exists()).toBe(false);
  wrapper.unmount();
});
it("공식 자료의 미검수 상태와 각 조각의 기간을 표시한다", async () => {
  detail.mockResolvedValue({
    data: {
      version: {
        material_type: "official",
        version: "1",
        effective_from: "2026-01-01T00:00:00+09:00",
        indexing_policy: { legal_temporal_reviewed: false },
        original_source: {},
      },
      sections: [
        {
          chunk_id: "chunk-1",
          text: "본문",
          effective_from: "2026-01-01T00:00:00+09:00",
          effective_until: "2026-12-31T00:00:00+09:00",
        },
        {
          chunk_id: "chunk-2",
          text: "예정 본문",
          effective_from: "2026-12-31T00:00:00+09:00",
          effective_until: null,
        },
      ],
      sections_meta: { page: 1, total_pages: 1 },
    },
  });
  const wrapper = mount(SourcePanel, {
    props: { source: { document_id: "doc", version_id: "ver" } },
    global: { directives: { "focus-trap": {} } },
  });
  await flushPromises();
  expect(wrapper.text()).toContain("법령 시행 시점 미검수");
  expect(wrapper.text()).toContain("종료 시각 미포함");
  expect(wrapper.text()).toContain("종료일 지정 없음");
  expect(wrapper.findAll(".source-excerpt")[1].text()).toContain(
    "2026. 12. 31.",
  );
  wrapper.unmount();
});

import DocumentsView from "../src/views/DocumentsView.vue";
async function openUpload() {
  list.mockResolvedValue({
    data: [],
    meta: { page: 1, total_pages: 1, total_items: 0 },
  });
  upload.mockReset().mockResolvedValue({});
  const wrapper = mount(DocumentsView, {
    global: { directives: { "focus-trap": {} } },
  });
  await flushPromises();
  const click = async (text) => {
    await wrapper
      .findAll("button")
      .find((button) => button.text() === text)
      .trigger("click");
    await flushPromises();
  };
  await click("문서 등록");
  await click("레시피 v1.1 등록 예시 채우기");
  const file = wrapper.get('input[type="file"]');
  Object.defineProperty(file.element, "files", {
    value: [
      new File(["test pdf"], "synthetic-test.pdf", { type: "application/pdf" }),
    ],
    configurable: true,
  });
  await file.trigger("change");
  return wrapper;
}
it("실제 등록 컴포넌트가 포함·제외 페이지를 API 메타데이터에 전달한다", async () => {
  const wrapper = await openUpload();
  await wrapper.get('input[placeholder="예: 3, 4, 5"]').setValue("3, 4, 3");
  await wrapper.get('input[placeholder="예: 1, 2"]').setValue("1, 2");
  await wrapper.get("form.upload-form").trigger("submit");
  await flushPromises();
  expect(upload).toHaveBeenCalledTimes(1);
  expect(upload.mock.calls[0][1].indexing_policy).toEqual({
    include_pages: [3, 4],
    exclude_pages: [1, 2],
    legal_temporal_reviewed: false,
  });
  expect(wrapper.text()).toContain("문서를 초안으로 등록했습니다");
  wrapper.unmount();
});
it("실제 등록 컴포넌트는 충돌한 페이지를 서버로 전송하지 않는다", async () => {
  const wrapper = await openUpload();
  await wrapper.get('input[placeholder="예: 3, 4, 5"]').setValue("3, 4");
  await wrapper.get('input[placeholder="예: 1, 2"]').setValue("4");
  await wrapper.get("form.upload-form").trigger("submit");
  await flushPromises();
  expect(upload).not.toHaveBeenCalled();
  expect(wrapper.text()).toContain("동시에 지정할 수 없습니다");
  wrapper.unmount();
});
