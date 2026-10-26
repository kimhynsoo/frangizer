// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
const m = vi.hoisted(() => ({ ask: vi.fn() }));
vi.mock("../src/stores/auth", () => ({
  useAuth: () => ({ user: { id: "owner-followup", role: "owner" } }),
}));
vi.mock("../src/api/rag", () => ({
  ask: m.ask,
  conversation: vi.fn(),
  feedback: vi.fn(),
}));
vi.mock("../src/api/voice", () => ({
  speakLocally: vi.fn(),
  recognitionAvailable: () => false,
  recordAudio: vi.fn(),
  browserListen: vi.fn(),
}));
import AskChat from "../src/components/AskChat.vue";
import AnswerDetails from "../src/components/AnswerDetails.vue";
import VoiceInput from "../src/components/VoiceInput.vue";
const prompt = "HOT과 ICED 중 어느 레시피인가요?";
const clarification = {
  question_id: "q1",
  conversation_id: "same-conversation",
  status: "needs_review",
  basis: "current",
  answer: prompt,
  steps: [],
  sources: [],
  follow_up_questions: [prompt, prompt],
};
let wrapper;
beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    value: vi.fn(),
    configurable: true,
  });
});
afterEach(() => wrapper?.unmount());
async function initial() {
  m.ask.mockResolvedValueOnce({ data: clarification });
  wrapper = mount(AskChat, { props: { active: true, tabId: "followup" } });
  await wrapper.find("textarea").setValue("바닐라라테 어떻게 만들어?");
  await wrapper.find("form").trigger("submit");
  await flushPromises();
}
it("본문과 같은 확인 질문은 한 번만 표시하고 온도 선택 버튼을 제공한다", async () => {
  await initial();
  expect(wrapper.text().split(prompt)).toHaveLength(2);
  expect(
    wrapper.findAll(".follow-up-choices button").map((b) => b.text()),
  ).toEqual(["핫", "아이스"]);
  expect(wrapper.find(".badge.needs_review").exists()).toBe(true);
});
it("핫 버튼은 새 대화가 아닌 기존 conversation_id로 보내며 응답 출처를 표시한다", async () => {
  await initial();
  m.ask.mockResolvedValueOnce({
    data: {
      ...clarification,
      question_id: "q2",
      status: "answered",
      answer: "핫 레시피입니다.",
      follow_up_questions: [],
      sources: [
        {
          chunk_id: "c1",
          title: "카페 레시피",
          page: 5,
          version: "1.0",
          material_type: "synthetic",
          is_demo: true,
        },
      ],
    },
  });
  await wrapper.findAll(".follow-up-choices button")[0].trigger("click");
  await flushPromises();
  expect(m.ask.mock.calls[1][0]).toEqual({
    question: "핫",
    conversation_id: "same-conversation",
  });
  expect(wrapper.text()).toContain("카페 레시피");
  expect(wrapper.text()).toContain("PDF 5쪽");
  expect(wrapper.find(".follow-up-choices").exists()).toBe(false);
});
it("음성으로 입력한 아이스도 같은 대화로 전달한다", async () => {
  await initial();
  m.ask.mockResolvedValueOnce({
    data: {
      ...clarification,
      question_id: "q2",
      answer: "아이스 레시피",
      status: "answered",
      follow_up_questions: [],
    },
  });
  wrapper
    .findComponent(VoiceInput)
    .vm.$emit("confirmed", { text: "아이스", readAnswer: false });
  await flushPromises();
  expect(m.ask.mock.calls[1][0]).toEqual({
    question: "아이스",
    conversation_id: "same-conversation",
  });
});
it("본문에 없는 확인 질문만 추가하며 임의의 선택지는 만들지 않는다", () => {
  wrapper = mount(AnswerDetails, {
    props: {
      result: {
        ...clarification,
        answer: "정보를 더 확인해야 합니다.",
        follow_up_questions: [
          "매장의 업종은 무엇인가요?",
          "매장의 업종은 무엇인가요?",
        ],
      },
      allowFollowUp: true,
    },
  });
  expect(wrapper.findAll(".follow-up-question")).toHaveLength(1);
  expect(wrapper.find(".follow-up-choices").exists()).toBe(false);
});
it("전송 중 연속 선택으로 질문을 중복 전송하지 않는다", async () => {
  await initial();
  let finish;
  m.ask.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
  const hot = wrapper.findAll(".follow-up-choices button")[0];
  await hot.trigger("click");
  expect(m.ask).toHaveBeenCalledTimes(2);
  expect(wrapper.find(".follow-up-choices").exists()).toBe(false);
  finish({
    data: {
      ...clarification,
      question_id: "q2",
      status: "answered",
      follow_up_questions: [],
    },
  });
  await flushPromises();
  expect(m.ask).toHaveBeenCalledTimes(2);
});
