// @vitest-environment happy-dom
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
const m = vi.hoisted(() => ({
  speak: vi.fn(),
  ask: vi.fn(),
  user: { id: "owner", role: "owner" },
}));
vi.mock("../src/stores/auth", () => ({ useAuth: () => ({ user: m.user }) }));
vi.mock("../src/api/rag", () => ({ ask: m.ask, feedback: vi.fn() }));
vi.mock("../src/api/voice", () => ({
  speakLocally: m.speak,
  recognitionAvailable: () => true,
  browserListen: vi.fn(),
  recordAudio: vi.fn(),
  voiceCommand: vi.fn(),
}));
import AskView from "../src/views/AskView.vue";
let wrapper;
const answer = "현재 시연에 등록된 자료로는 답을 확인할 수 없습니다.";
const button = (text) =>
  wrapper.findAll("button").find((b) => b.text().includes(text));
beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
  m.user.role = "owner";
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    value: vi.fn(),
    configurable: true,
  });
  m.ask.mockResolvedValue({
    data: {
      question_id: "q1",
      conversation_id: "conv1",
      answer,
      steps: [],
      sources: [],
      basis: "current",
      status: "needs_review",
      handoff_reason: "상태 안내",
      follow_up_questions: [],
    },
  });
  m.speak.mockImplementation(
    (_text, { signal }) =>
      new Promise((resolve) =>
        signal.addEventListener("abort", () => resolve(false), { once: true }),
      ),
  );
});
afterEach(() => wrapper?.unmount());
async function send(auto) {
  wrapper = mount(AskView);
  if (auto) await button("답변 자동 읽기 꺼짐").trigger("click");
  await wrapper.get("textarea").setValue("가맹 준비 질문");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
}
it("자동 읽기는 답변 본문부터 시작하고 고정 중지 버튼에서 취소된다", async () => {
  await send(true);
  expect(m.speak).toHaveBeenCalledTimes(1);
  expect(m.speak.mock.calls[0][0].trim()).toBe(answer);
  expect(wrapper.get(".answer-details button").text()).toContain("읽기 중지");
  const signal = m.speak.mock.calls[0][1].signal;
  await wrapper.get(".reading-stop").trigger("click");
  await flushPromises();
  expect(signal.aborted).toBe(true);
  expect(wrapper.find(".reading-stop").exists()).toBe(false);
  expect(wrapper.get(".answer-details button").text()).toContain(
    "답변 읽어주기",
  );
});
it("수동 읽기도 고정 버튼과 상태를 공유하며 답변 아래에서 중지된다", async () => {
  await send(false);
  expect(m.speak).not.toHaveBeenCalled();
  await button("답변 읽어주기").trigger("click");
  expect(wrapper.find(".reading-stop").exists()).toBe(true);
  const signal = m.speak.mock.calls[0][1].signal;
  await wrapper.get(".answer-details button").trigger("click");
  await flushPromises();
  expect(signal.aborted).toBe(true);
  expect(wrapper.find(".reading-stop").exists()).toBe(false);
});
it("예비 가맹점주에게 재생·중지 버튼이 나타나지 않는다", async () => {
  m.user.role = "prospect";
  await send(false);
  expect(wrapper.text()).not.toContain("답변 읽어주기");
  expect(wrapper.text()).not.toContain("답변 자동 읽기");
  expect(wrapper.find(".reading-stop").exists()).toBe(false);
  expect(m.speak).not.toHaveBeenCalled();
});
