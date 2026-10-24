// @vitest-environment happy-dom
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { defineComponent } from "vue";
import { createPinia } from "pinia";
const m = vi.hoisted(() => ({
  ask: vi.fn(),
  conversation: vi.fn(),
  speak: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("../src/stores/auth", () => ({
  useAuth: () => ({ user: { id: "owner-tabs", role: "owner" } }),
}));
vi.mock("../src/api/rag", () => ({
  ask: m.ask,
  conversation: m.conversation,
  conversationMessages: (messages) => messages,
  feedback: vi.fn(),
}));
vi.mock("../src/api/voice", () => ({
  speakLocally: m.speak,
  recognitionAvailable: () => true,
  browserListen: vi.fn(),
  recordAudio: vi.fn(),
  voiceCommand: vi.fn(),
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ name: "ask" }),
  useRouter: () => ({ push: m.navigate }),
}));
import AskView from "../src/views/AskView.vue";
import ChatNavigation from "../src/components/ChatNavigation.vue";
const ChatWorkspace = defineComponent({
  components: { AskView, ChatNavigation },
  props: { showChat: { type: Boolean, default: true } },
  template: '<div><ChatNavigation /><AskView v-if="showChat" /></div>',
});
const mountWorkspace = () =>
  mount(ChatWorkspace, { global: { plugins: [createPinia()] } });
let wrapper;
const panel = () =>
  wrapper.get(
    `[id="${wrapper.get('[role="tab"][aria-selected="true"]').attributes("aria-controls")}"]`,
  );
const tabs = () => wrapper.findAll('[role="tab"]');
const result = (id) => ({
  data: {
    question_id: `q-${id}`,
    conversation_id: id,
    answer: `답변 ${id}`,
    steps: [],
    sources: [],
    status: "answered",
  },
});
async function send(text) {
  await panel().get("textarea").setValue(text);
  await panel().get("form").trigger("submit");
  await flushPromises();
}
async function add() {
  await wrapper.get(".new-chat-button").trigger("click");
}
beforeEach(() => {
  sessionStorage.clear();
  vi.resetAllMocks();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    value: vi.fn(),
    configurable: true,
  });
  m.conversation.mockResolvedValue({ data: { messages: [] } });
  m.speak.mockImplementation(
    (_text, { signal }) =>
      new Promise((resolve) =>
        signal.addEventListener("abort", () => resolve(false), { once: true }),
      ),
  );
});
afterEach(() => wrapper?.unmount());
it("새 탭은 새 conversation_id로 시작하고 원래 탭의 대화와 초안을 유지한다", async () => {
  m.ask
    .mockResolvedValueOnce(result("first"))
    .mockResolvedValueOnce(result("second"))
    .mockResolvedValueOnce(result("first"));
  wrapper = mountWorkspace();
  await send("첫 질문");
  expect(m.ask.mock.calls[0][0]).not.toHaveProperty("as_of");
  expect(wrapper.find('input[type="datetime-local"]').exists()).toBe(false);
  await panel().get("textarea").setValue("작성 중인 질문");
  await add();
  expect(panel().find(".message-pair").exists()).toBe(false);
  expect(panel().get("textarea").element.value).toBe("");
  await send("별도 질문");
  expect(m.ask.mock.calls[1][0].conversation_id).toBe(null);
  await tabs()[0].trigger("click");
  expect(panel().text()).toContain("답변 first");
  expect(panel().text()).not.toContain("답변 second");
  expect(panel().get("textarea").element.value).toBe("작성 중인 질문");
  await send("후속 질문");
  expect(m.ask.mock.calls[2][0].conversation_id).toBe("first");
  const ids = wrapper.findAll("textarea").map((t) => t.attributes("id"));
  expect(new Set(ids).size).toBe(ids.length);
});
it("이전 탭에서 늦게 도착한 답변은 새 탭에 섞이지 않고 자동 재생하지 않는다", async () => {
  let resolve;
  m.ask.mockReturnValueOnce(
    new Promise((r) => {
      resolve = r;
    }),
  );
  wrapper = mountWorkspace();
  await panel()
    .findAll("button")
    .find((b) => b.text().includes("답변 자동 읽기 꺼짐"))
    .trigger("click");
  await send("대기 중인 질문");
  expect(panel().get("textarea").element.value).toBe("");
  expect(panel().get(".question-bubble").text()).toContain("대기 중인 질문");
  await add();
  resolve(result("delayed"));
  await flushPromises();
  expect(panel().find(".message-pair").exists()).toBe(false);
  expect(m.speak).not.toHaveBeenCalled();
  await tabs()[0].trigger("click");
  expect(panel().text()).toContain("답변 delayed");
});
it("새로고침 후 탭별 대화 ID와 작성 중인 초안을 복원한다", async () => {
  m.ask.mockResolvedValue(result("saved"));
  wrapper = mountWorkspace();
  await send("저장된 질문");
  await add();
  await panel().get("textarea").setValue("두 번째 탭 초안");
  wrapper.unmount();
  wrapper = mountWorkspace();
  await flushPromises();
  expect(tabs()).toHaveLength(2);
  expect(panel().get("textarea").element.value).toBe("두 번째 탭 초안");
  expect(m.conversation).toHaveBeenCalledWith("saved");
});

it("선택하지 않은 탭 삭제는 현재 대화를 유지하고 선택한 탭 삭제는 옆 탭으로 이동한다", async () => {
  wrapper = mountWorkspace();
  await panel().get("textarea").setValue("삭제할 초안");
  const removedId = tabs()[0].attributes("id").replace("chat-tab-", "");
  await add();
  const active = tabs()[1].attributes("id");
  await wrapper.findAll(".chat-tab-close")[0].trigger("click");
  expect(tabs()).toHaveLength(1);
  expect(tabs()[0].attributes("id")).toBe(active);
  expect(
    sessionStorage.getItem(`frangizer.question-draft.owner-tabs.${removedId}`),
  ).toBe(null);
  await add();
  await wrapper.findAll(".chat-tab-close")[1].trigger("click");
  expect(tabs()[0].attributes("aria-selected")).toBe("true");
  wrapper.unmount();
  wrapper = mountWorkspace();
  expect(tabs()).toHaveLength(1);
  expect(panel().get("textarea").element.value).toBe("");
});
it("답변 대기 중인 마지막 탭 삭제는 빈 탭을 열고 늦은 답변을 복원하지 않는다", async () => {
  let resolve;
  m.ask.mockReturnValueOnce(
    new Promise((r) => {
      resolve = r;
    }),
  );
  wrapper = mountWorkspace();
  await send("삭제할 질문");
  await wrapper.get(".chat-tab-close").trigger("click");
  expect(tabs()).toHaveLength(1);
  expect(panel().get("textarea").element.value).toBe("");
  resolve(result("deleted"));
  await flushPromises();
  expect(panel().find(".message-pair").exists()).toBe(false);
  const saved = JSON.parse(
    sessionStorage.getItem("frangizer.chat-tabs.owner-tabs"),
  );
  expect(saved.tabs[0].conversationId).toBe(null);
});

it("질문 화면을 떠나도 사이드바 목록을 유지하고 선택한 대화로 돌아온다", async () => {
  m.ask.mockResolvedValue(result("retained"));
  wrapper = mountWorkspace();
  await send("유지할 대화");
  await wrapper.setProps({ showChat: false });
  expect(wrapper.find(".ask-tabs-page").exists()).toBe(false);
  expect(wrapper.get(".sidebar-conversations").text()).toContain("유지할 대화");
  await wrapper.get('[role="tab"]').trigger("click");
  expect(m.navigate).toHaveBeenCalledWith("/ask");
  await wrapper.setProps({ showChat: true });
  await flushPromises();
  expect(m.conversation).toHaveBeenCalledWith("retained");
  await send("이어서 질문");
  expect(m.ask.mock.calls.at(-1)[0].conversation_id).toBe("retained");
});
