import { afterEach, expect, it, vi } from "vitest";
import { createHandsFree, speakWithWakeInterrupt } from "../src/api/hands-free";
afterEach(() => vi.useRealTimers());
const pending = ({ signal }) =>
  new Promise((resolve) =>
    signal.addEventListener("abort", () => resolve(false), { once: true }),
  );
const pendingListen = ({ signal }) =>
  new Promise((_, reject) =>
    signal.addEventListener(
      "abort",
      () => reject(new DOMException("stop", "AbortError")),
      { once: true },
    ),
  );
it("읽는 중 호출어와 새 질문이 들어오면 기존 읽기를 중지하고 정확히 한 번 새 질문을 전송한다", async () => {
  vi.useFakeTimers();
  let firstSignal;
  const ask = vi
    .fn()
    .mockResolvedValueOnce({ answer: "첫 답변" })
    .mockResolvedValueOnce({ answer: "새 답변" });
  const listen = vi
    .fn()
    .mockResolvedValueOnce("시리 첫 질문")
    .mockResolvedValueOnce("쉬리 새 질문")
    .mockImplementation(pendingListen);
  const speak = vi.fn((text, options) => {
    if (text === "첫 답변") {
      firstSignal = options.signal;
      return pending(options);
    }
    return Promise.resolve(true);
  });
  const session = createHandsFree({
    ask,
    listen,
    speak,
    getAliases: () => ["쉬리"],
  });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(0);
  expect(firstSignal.aborted).toBe(true);
  expect(ask.mock.calls.map(([q]) => q)).toEqual(["첫 질문", "새 질문"]);
  expect(speak.mock.calls.map(([text]) => text)).toEqual([
    "첫 답변",
    "새 답변",
  ]);
  session.stop();
  await running;
});
it("읽는 중 호출어만 말하면 멈춘 다음 질문을 다시 듣는다", async () => {
  vi.useFakeTimers();
  const ask = vi
    .fn()
    .mockResolvedValueOnce({ answer: "첫 답변" })
    .mockResolvedValueOnce({ answer: "새 답변" });
  const listen = vi
    .fn()
    .mockResolvedValueOnce("시리 첫 질문")
    .mockResolvedValueOnce("시리")
    .mockResolvedValueOnce("다음 질문")
    .mockImplementation(pendingListen);
  const speak = vi.fn((text, options) =>
    text === "첫 답변" ? pending(options) : Promise.resolve(true),
  );
  const session = createHandsFree({ ask, listen, speak });
  const running = session.start();
  await vi.advanceTimersByTimeAsync(0);
  expect(ask.mock.calls.map(([q]) => q)).toEqual(["첫 질문", "다음 질문"]);
  expect(speak.mock.calls.map(([text]) => text)).toEqual([
    "첫 답변",
    "네, 말씀하세요",
    "새 답변",
  ]);
  session.stop();
  await running;
});
it("대기 끄기는 재생과 읽는 중 마이크를 모두 중지한다", async () => {
  vi.useFakeTimers();
  const controller = new AbortController();
  let reading, listening;
  const promise = speakWithWakeInterrupt("답변", {
    signal: controller.signal,
    speak: (_, options) => {
      reading = options.signal;
      return pending(options);
    },
    listen: (options) => {
      listening = options.signal;
      return pendingListen(options);
    },
  });
  await vi.advanceTimersByTimeAsync(0);
  controller.abort();
  await promise;
  expect(reading.aborted).toBe(true);
  expect(listening.aborted).toBe(true);
});
it("일반 말소리로 답변을 멈추지 않는다", async () => {
  vi.useFakeTimers();
  const controller = new AbortController();
  let finish;
  const speak = vi.fn(() => new Promise((resolve) => (finish = resolve)));
  const listen = vi
    .fn()
    .mockResolvedValueOnce("손님 주문 나왔습니다")
    .mockImplementation(pendingListen);
  const promise = speakWithWakeInterrupt("답변", {
    signal: controller.signal,
    speak,
    listen,
  });
  await vi.advanceTimersByTimeAsync(350);
  expect(speak.mock.calls[0][1].signal.aborted).toBe(false);
  finish(true);
  const result = await promise;
  expect(result.heard).toBeNull();
});
it("기기가 재생 중 마이크를 지원하지 않아도 현재 답변을 마저 읽는다", async () => {
  let finish;
  const controller = new AbortController();
  const promise = speakWithWakeInterrupt("답변", {
    signal: controller.signal,
    speak: () => new Promise((resolve) => (finish = resolve)),
    listen: vi.fn().mockRejectedValue(new Error("audio unavailable")),
  });
  await Promise.resolve();
  finish(true);
  expect(await promise).toEqual({
    heard: null,
    ok: true,
    monitorUnavailable: true,
  });
});
