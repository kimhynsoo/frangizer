import { afterEach, expect, it, vi } from "vitest";
import { createHandsFree, wakeQuestion } from "../src/api/hands-free";
afterEach(() => vi.useRealTimers());
const pendingListen = ({ signal }) =>
  new Promise((_, reject) =>
    signal.addEventListener(
      "abort",
      () => reject(new DOMException("stop", "AbortError")),
      { once: true },
    ),
  );
function setup(overrides = {}) {
  const state = vi.fn(),
    ask = vi.fn().mockResolvedValue({
      answer: "시럽 두 펌프입니다.",
      steps: ["우유를 넣으세요"],
    }),
    speak = vi.fn().mockResolvedValue(true);
  const listen = vi
    .fn()
    .mockResolvedValueOnce("시리")
    .mockResolvedValueOnce("바닐라라테 시럽 몇 펌프야")
    .mockImplementation(pendingListen);
  return {
    ask,
    speak,
    listen,
    state,
    session: createHandsFree({
      ask,
      speak,
      listen,
      onState: state,
      ...overrides,
    }),
  };
}
it.each([
  ["시리", ""],
  ["시 리", ""],
  ["헤이 시리", ""],
  ["시리야", ""],
  ["Siri", ""],
  ["시리 바닐라라테 알려줘", "바닐라라테 알려줘"],
  ["시리야, 시럽 몇 펌프야", "시럽 몇 펌프야"],
  ["프랜자이저", null],
  ["프랜차이즈", null],
  ["프렌자이저", null],
  ["이름은 시리야", null],
  ["시리즈 알려줘", null],
  ["바닐라라테", null],
])("호출어 판정: %s", (text, result) =>
  expect(wakeQuestion(text)).toBe(result),
);
it("호출 → 안내 → 질문 → API → 읽기 중 호출어를 듣고 완료 후 다시 대기한다", async () => {
  vi.useFakeTimers();
  const { session, ask, speak, listen, state } = setup();
  const running = session.start();
  await vi.advanceTimersByTimeAsync(0);
  expect(ask).toHaveBeenCalledWith("바닐라라테 시럽 몇 펌프야");
  expect(speak.mock.calls.map(([text]) => text)).toEqual([
    "네, 말씀하세요",
    "시럽 두 펌프예요.",
  ]);
  expect(state.mock.calls.map(([s]) => s)).toEqual([
    "off",
    "waiting",
    "prompting",
    "question",
    "thinking",
    "speaking",
  ]);
  expect(listen).toHaveBeenCalledTimes(3);
  await vi.advanceTimersByTimeAsync(350);
  expect(listen).toHaveBeenCalledTimes(4);
  expect(state).toHaveBeenLastCalledWith("waiting");
  session.stop();
  await running;
});
it("호출어와 함께 말한 질문은 재녹음 없이 한 번 전송한다", async () => {
  vi.useFakeTimers();
  const listen = vi
    .fn()
    .mockResolvedValueOnce("시리 시럽 몇 펌프야")
    .mockImplementation(pendingListen);
  const { session, ask, speak } = setup({ listen });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(0);
  expect(ask).toHaveBeenCalledOnce();
  expect(ask).toHaveBeenCalledWith("시럽 몇 펌프야");
  expect(speak).toHaveBeenCalledOnce();
  session.stop();
  await running;
});
it("일반 대화는 질문 API로 보내지 않는다", async () => {
  vi.useFakeTimers();
  const listen = vi
    .fn()
    .mockResolvedValueOnce("주문 들어왔어요")
    .mockImplementation(pendingListen);
  const { session, ask } = setup({ listen });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(350);
  expect(ask).not.toHaveBeenCalled();
  session.stop();
  await running;
});
it("질문 취소는 전송하지 않고 다시 대기한다", async () => {
  vi.useFakeTimers();
  const listen = vi
    .fn()
    .mockResolvedValueOnce("시리")
    .mockResolvedValueOnce("취소")
    .mockImplementation(pendingListen);
  const { session, ask } = setup({ listen });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(350);
  expect(ask).not.toHaveBeenCalled();
  session.stop();
  await running;
});
it("중지 후 늦게 온 응답은 읽거나 인식을 재시작하지 않는다", async () => {
  vi.useFakeTimers();
  let finish;
  const ask = vi.fn(() => new Promise((resolve) => (finish = resolve)));
  const { session, speak, listen } = setup({ ask });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(0);
  session.stop();
  finish({ answer: "늦은 응답" });
  await running;
  expect(speak).toHaveBeenCalledTimes(1);
  expect(listen).toHaveBeenCalledTimes(2);
});
it("예비 점주의 답변은 읽지 않는다", async () => {
  vi.useFakeTimers();
  const { session, speak } = setup({ allowReadAloud: false });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(0);
  expect(speak.mock.calls.map(([text]) => text)).toEqual(["네, 말씀하세요"]);
  session.stop();
  await running;
});
it("인식 오류는 재시도 폭주 없이 종료한다", async () => {
  const onError = vi.fn(),
    listen = vi.fn().mockRejectedValue(new Error("마이크 권한이 필요합니다."));
  const { session, state } = setup({ listen, onError });
  await session.start();
  expect(listen).toHaveBeenCalledOnce();
  expect(onError).toHaveBeenCalledWith("마이크 권한이 필요합니다.");
  expect(state).toHaveBeenLastCalledWith("off");
});
