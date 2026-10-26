// @vitest-environment happy-dom
import { expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { pagePolicy, indexJobFailure } from "../src/api/documents";
import ErrorNotice from "../src/components/ErrorNotice.vue";
const { detail, list, upload, versions } = vi.hoisted(() => ({
  detail: vi.fn(),
  list: vi.fn(),
  upload: vi.fn(),
  versions: vi.fn(),
}));
vi.mock("../src/stores/auth", () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock("../src/api/documents", async (original) => ({
  ...(await original()),
  detail,
  list,
  upload,
  versions,
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

async function openVersionHistory() {
  list.mockResolvedValue({
    data: [
      {
        document_id: "history-doc",
        title: "판본 확인 문서",
        selected_version: "1.0",
        selected_version_id: "history-doc-1.0",
        lifecycle: "published",
        index_status: "ready",
        category: "recipe",
        effective_from: "2026-10-01T00:00:00+09:00",
      },
    ],
    meta: { page: 1, total_pages: 1, total: 1 },
  });
  const wrapper = mount(DocumentsView, {
    global: { directives: { "focus-trap": {} } },
  });
  await flushPromises();
  await wrapper
    .findAll("button")
    .find((b) => b.text().includes("판본 보기"))
    .trigger("click");
  return wrapper;
}
it("판본 보기는 즉시 팝업과 로딩 상태를 열고 닫은 뒤 도착한 응답은 무시한다", async () => {
  let complete;
  versions.mockReturnValueOnce(
    new Promise((resolve) => {
      complete = resolve;
    }),
  );
  const wrapper = await openVersionHistory();
  const dialog = wrapper.get('[role="dialog"]');
  expect(dialog.text()).toContain("판본 확인 문서 · 판본 이력");
  expect(dialog.get('[role="status"]').text()).toContain("불러오는 중");
  await dialog.get('[aria-label="판본 이력 닫기"]').trigger("click");
  complete({ data: [], meta: { page: 1, total_pages: 0 } });
  await flushPromises();
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
  wrapper.unmount();
});
it("판본 조회 실패는 팝업 안에 원인과 다시 시도 버튼을 표시한다", async () => {
  versions.mockRejectedValueOnce({
    message: "판본 조회 실패",
    retryable: true,
  });
  const wrapper = await openVersionHistory();
  await flushPromises();
  const dialog = wrapper.get('[role="dialog"]');
  expect(dialog.text()).toContain("판본 조회 실패");
  expect(dialog.text()).toContain("다시 시도");
  wrapper.unmount();
});

const recipeRow = {
  document_id: "demo-cafe-recipes",
  title: "카페 표준 레시피 북 (합성)",
  selected_version: "1.0",
  selected_version_id: "demo-cafe-recipes-1.0",
  lifecycle: "published",
  index_status: "ready",
  category: "recipe",
  effective_from: "2026-10-01T00:00:00+09:00",
};
async function fillAndSubmit(wrapper, version) {
  const set = (selector, value) => wrapper.get(selector).setValue(value);
  const file = wrapper.get('input[type="file"]');
  Object.defineProperty(file.element, "files", {
    value: [new File(["pdf"], "v.pdf", { type: "application/pdf" })],
    configurable: true,
  });
  await file.trigger("change");
  await set('input[type="datetime-local"]', "2026-10-26T12:00");
  for (const input of wrapper.findAll("form.upload-form input[required]"))
    if (!input.element.value && input.attributes("type") !== "file")
      await input.setValue(
        input.attributes("type") === "date" ? "2026-10-26" : "값",
      );
  await set('input[maxlength="50"]', version);
  await wrapper.get("form.upload-form").trigger("submit");
  await flushPromises();
}
it("문서 줄의 새 판본은 문서를 고정하고 현재 판본 값을 채워 같은 문서로 등록한다", async () => {
  list.mockResolvedValue({
    data: [recipeRow],
    meta: { page: 1, total_pages: 1, total: 1 },
  });
  versions.mockResolvedValue({
    data: [
      {
        ...recipeRow,
        version_id: "demo-cafe-recipes-1.0",
        version: "1.0",
        material_type: "synthetic",
        visibility: "owner",
        original_source: {
          title: "합성 레시피",
          publisher: "프로젝트 팀",
          url: null,
          note: "합성",
        },
      },
    ],
    meta: { page: 1, total_pages: 1, total: 1 },
  });
  upload.mockReset().mockResolvedValue({});
  const wrapper = mount(DocumentsView, {
    global: { directives: { "focus-trap": {} } },
  });
  await flushPromises();
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "새 판본")
    .trigger("click");
  await flushPromises();
  const dialog = wrapper.get('[aria-labelledby="upload-title"]');
  expect(dialog.text()).toContain("카페 표준 레시피 북 (합성) · 새 판본 등록");
  expect(dialog.text()).toContain("현재 v1.0");
  expect(dialog.text()).not.toContain("기존 문서 ID");
  await fillAndSubmit(wrapper, "1.1");
  const metadata = upload.mock.calls[0][1];
  expect(metadata.document_id).toBe("demo-cafe-recipes");
  expect(metadata.title).toBe("카페 표준 레시피 북 (합성)");
  expect(metadata.visibility).toBe("owner");
  expect(metadata.original_source.publisher).toBe("프로젝트 팀");
  wrapper.unmount();
});
it("상단 문서 등록은 문서 ID 없이 새 문서로 등록한다", async () => {
  list.mockResolvedValue({
    data: [recipeRow],
    meta: { page: 1, total_pages: 1, total: 1 },
  });
  upload.mockReset().mockResolvedValue({});
  const wrapper = mount(DocumentsView, {
    global: { directives: { "focus-trap": {} } },
  });
  await flushPromises();
  await wrapper
    .findAll("button")
    .find((b) => b.text() === "문서 등록")
    .trigger("click");
  await flushPromises();
  expect(wrapper.get('[aria-labelledby="upload-title"]').text()).toContain(
    "새 문서 등록",
  );
  await wrapper.get('input[maxlength="200"]').setValue("새 위생 안내");
  await fillAndSubmit(wrapper, "1.0");
  expect(upload).toHaveBeenCalledTimes(1);
  expect(upload.mock.calls[0][1]).not.toHaveProperty("document_id");
  wrapper.unmount();
});
