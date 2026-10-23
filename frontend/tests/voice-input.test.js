// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
const mocks = vi.hoisted(() => ({ listen: vi.fn(), speak: vi.fn() }));
vi.mock("../src/api/voice", () => ({
  recognitionAvailable: () => true,
  browserListen: mocks.listen,
  recordAudio: vi.fn(),
  voiceCommand: (text) =>
    text === "보내기"
      ? "send"
      : text === "다시"
        ? "retry"
        : text === "취소"
          ? "cancel"
          : null,
  speakLocally: mocks.speak,
}));
import VoiceInput from "../src/components/VoiceInput.vue";
let wrapper;
const button = (label) =>
  wrapper
    .findAll("button")
    .find((node) =>
      (node.attributes("aria-label") || node.text()).includes(label),
    );
beforeEach(() => {
  vi.clearAllMocks();
  mocks.speak.mockResolvedValue(false);
});
afterEach(() => wrapper?.unmount());
it("인식만으로 질문하지 않고 별도의 음성 확인 뒤에만 보낸다", async () => {
  let confirm;
  mocks.listen
    .mockImplementationOnce(async (options) => {
      options.onStopReady(() => {});
      return "아이스 바닐라라테 시럽 몇 펌프야?";
    })
    .mockImplementationOnce((options) => {
      options.onStopReady(() => {});
      return new Promise((resolve) => (confirm = resolve));
    });
  wrapper = mount(VoiceInput);
  await button("말로 질문하기").trigger("click");
  await flushPromises();
  expect(wrapper.get("textarea").element.value).toContain("아이스 바닐라라테");
  expect(wrapper.emitted("confirmed")).toBeUndefined();
  confirm("보내기");
  await flushPromises();
  expect(wrapper.emitted("confirmed")).toEqual([
    [{ text: "아이스 바닐라라테 시럽 몇 펌프야?", readAnswer: true }],
  ]);
});
it("확인 명령이 불명확하면 질문을 보내지 않고 검토 상태를 유지한다", async () => {
  mocks.listen
    .mockResolvedValueOnce("바닐라라테 알려줘")
    .mockResolvedValueOnce("안 보내");
  wrapper = mount(VoiceInput);
  await button("말로 질문하기").trigger("click");
  await flushPromises();
  expect(wrapper.emitted("confirmed")).toBeUndefined();
  expect(wrapper.text()).toContain("질문은 전송하지 않았습니다.");
});
it("취소·화면 이탈은 인식 요청의 AbortSignal을 취소한다", async () => {
  let signal;
  mocks.listen.mockImplementation((options) => {
    signal = options.signal;
    options.onStopReady(() => {});
    return new Promise(() => {});
  });
  wrapper = mount(VoiceInput);
  await button("말로 질문하기").trigger("click");
  await flushPromises();
  wrapper.unmount();
  expect(signal.aborted).toBe(true);
  wrapper = null;
});
it("녹음 없는 예시는 실제 인식으로 표시하지 않으며 명시적 확인이 필요하다", async () => {
  wrapper = mount(VoiceInput);
  await button("녹음 없이").trigger("click");
  expect(wrapper.text()).toContain("실제 음성 인식 아님");
  expect(mocks.listen).not.toHaveBeenCalled();
  expect(wrapper.emitted("confirmed")).toBeUndefined();
  await wrapper.get("form").trigger("submit");
  expect(wrapper.emitted("confirmed")).toHaveLength(1);
});

it("타이핑과 음성이 하나의 입력창과 전송 버튼을 공유하고 인식 문장도 직접 수정한다", async () => {
  mocks.listen.mockResolvedValueOnce("시럽 몇 펌프야?");
  wrapper = mount(VoiceInput);
  await wrapper.get('input[type="checkbox"]').setValue(false);
  await wrapper.get("textarea").setValue("아이스 바닐라라테");
  await button("말로 질문하기").trigger("click");
  await flushPromises();
  expect(wrapper.findAll("textarea")).toHaveLength(1);
  expect(wrapper.findAll('[aria-label="질문 보내기"]')).toHaveLength(1);
  expect(wrapper.get("textarea").element.value).toBe(
    "아이스 바닐라라테 시럽 몇 펌프야?",
  );
  expect(wrapper.emitted("confirmed")).toBeUndefined();
  await wrapper.get("textarea").setValue("아이스 바닐라라테 우유 몇 mL야?");
  await wrapper.get("form").trigger("submit");
  expect(wrapper.emitted("confirmed")[0][0].text).toBe(
    "아이스 바닐라라테 우유 몇 mL야?",
  );
});
it("타이핑만 해도 같은 전송 동작을 사용한다", async () => {
  wrapper = mount(VoiceInput);
  await wrapper.get("textarea").setValue("음료 제조 순서 알려줘");
  await wrapper.get("form").trigger("submit");
  expect(wrapper.emitted("confirmed")[0][0].text).toBe("음료 제조 순서 알려줘");
  expect(mocks.listen).not.toHaveBeenCalled();
});
it("취소하면 부분 인식 결과 대신 원래 작성 중이던 내용을 남긴다", async () => {
  mocks.listen.mockImplementation((options) => {
    options.onStopReady(() => {});
    options.onPartial("인식 중간 문장");
    return new Promise(() => {});
  });
  wrapper = mount(VoiceInput, { props: { modelValue: "작성 중인 질문" } });
  await button("말로 질문하기").trigger("click");
  await flushPromises();
  expect(wrapper.get("textarea").element.value).toContain("인식 중간 문장");
  await button("음성 입력 취소").trigger("click");
  expect(wrapper.get("textarea").element.value).toBe("작성 중인 질문");
  expect(wrapper.emitted("confirmed")).toBeUndefined();
});
