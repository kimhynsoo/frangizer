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
    "네",
    "시럽 두 펌프예요.",
  ]);
  expect(listen.mock.calls[1][0].stopOnFinal("바닐라라테 시럽 몇 펌프야")).toBe(
    true,
  );
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
it("음성 질문의 핫·아이스 확인은 호출어 없이 바로 듣고 같은 흐름으로 전송한다", async () => {
  vi.useFakeTimers();
  const clarification = {
    status: "needs_review",
    answer: "HOT과 ICED 중 어느 레시피인가요?",
    follow_up_questions: ["HOT과 ICED 중 어느 레시피인가요?"],
  };
  const ask = vi
    .fn()
    .mockResolvedValueOnce(clarification)
    .mockResolvedValueOnce({
      status: "answered",
      answer: "아이스는 시럽 두 펌프예요.",
      follow_up_questions: [],
    });
  const listen = vi
    .fn()
    .mockResolvedValueOnce("시리 바닐라라테 레시피 알려 줘")
    .mockResolvedValueOnce("차가운 걸로 해 줘")
    .mockImplementation(pendingListen);
  const { session, speak, state } = setup({ ask, listen });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(0);
  expect(ask.mock.calls.map(([question]) => question)).toEqual([
    "바닐라라테 레시피 알려 줘",
    "아이스",
  ]);
  expect(speak.mock.calls.map(([text]) => text)).toEqual([
    "HOT과 ICED 중 어느 레시피인가요?",
    "아이스는 시럽 두 펌프예요.",
  ]);
  expect(state.mock.calls.map(([value]) => value)).toContain("question");
  session.stop();
  await running;
});
it("일반 후속 질문도 호출어 없이 답을 바로 듣는다", async () => {
  vi.useFakeTimers();
  const ask = vi
    .fn()
    .mockResolvedValueOnce({
      status: "needs_review",
      answer: "어느 지역의 매장인가요?",
      follow_up_questions: ["어느 지역의 매장인가요?"],
    })
    .mockResolvedValueOnce({
      status: "answered",
      answer: "서울 지역 기준을 안내할게요.",
      follow_up_questions: [],
    });
  const listen = vi
    .fn()
    .mockResolvedValueOnce("시리 위생 기준 알려 줘")
    .mockResolvedValueOnce("서울이야")
    .mockImplementation(pendingListen);
  const { session } = setup({ ask, listen });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(0);
  expect(ask.mock.calls.map(([question]) => question)).toEqual([
    "위생 기준 알려 줘",
    "서울이야",
  ]);
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
  expect(speak.mock.calls.map(([text]) => text)).toEqual(["네"]);
  session.stop();
  await running;
});
it("인식 오류도 자동으로 끄지 않고 재연결을 기다리며 요청을 반복하지 않는다", async () => {
  vi.useFakeTimers();
  const onError = vi.fn(),
    listen = vi
      .fn()
      .mockRejectedValueOnce(new Error("마이크 권한이 필요합니다."))
      .mockImplementation(pendingListen);
  const { session, state } = setup({ listen, onError });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(0);
  expect(listen).toHaveBeenCalledOnce();
  expect(state).toHaveBeenLastCalledWith("paused");
  await vi.advanceTimersByTimeAsync(10000);
  expect(listen).toHaveBeenCalledOnce();
  session.retry();
  await vi.advanceTimersByTimeAsync(0);
  expect(state).toHaveBeenLastCalledWith("waiting");
  expect(listen).toHaveBeenCalledTimes(2);
  session.stop();
  await running;
  expect(state).toHaveBeenLastCalledWith("off");
});
it.each([
  undefined,
  {
    status: "needs_review",
    answer: "자료에서 답을 확인하지 못했습니다.",
    steps: [],
  },
])(
  "답변이 없거나 근거 부족이어도 다시 호출어를 기다린다: %j",
  async (result) => {
    vi.useFakeTimers();
    const ask = vi.fn().mockResolvedValue(result);
    const { session, state } = setup({ ask });
    const running = session.start();
    await vi.advanceTimersByTimeAsync(350);
    expect(state).toHaveBeenLastCalledWith("waiting");
    expect(ask).toHaveBeenCalledOnce();
    expect(state.mock.calls.filter(([value]) => value === "off")).toHaveLength(
      1,
    );
    session.stop();
    await running;
  },
);
it("답변 API 오류 후에도 다시 호출하면 새 질문을 처리한다", async () => {
  vi.useFakeTimers();
  const ask = vi
    .fn()
    .mockRejectedValueOnce(new Error("502"))
    .mockResolvedValueOnce({ answer: "두 펌프입니다." });
  const listen = vi
    .fn()
    .mockResolvedValueOnce("시리 첫 질문")
    .mockResolvedValueOnce("시리 다음 질문")
    .mockImplementation(pendingListen);
  const { session, state } = setup({ ask, listen });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(700);
  expect(ask.mock.calls.map(([question]) => question)).toEqual([
    "첫 질문",
    "다음 질문",
  ]);
  expect(state).toHaveBeenLastCalledWith("waiting");
  session.stop();
  await running;
});
