import { afterEach, expect, it, vi } from "vitest";
import { preferredKoreanVoice, speakLocally } from "../src/api/voice";
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
const basic = { name: "Yuna", lang: "ko-KR", localService: true };
it("설치된 한국어 고품질 음성을 우선하며 원격 음성을 자동 선택하지 않는다", () => {
  const enhanced = { ...basic, name: "Yuna Enhanced" };
  expect(
    preferredKoreanVoice([
      basic,
      enhanced,
      { ...basic, name: "Premium Cloud", localService: false },
    ]),
  ).toBe(enhanced);
});
it("음성 목록이 늦게 로딩돼도 기다린 뒤 정상 속도로 재생한다", async () => {
  vi.useFakeTimers();
  let voices = [],
    changed;
  const synthesis = {
    getVoices: () => voices,
    addEventListener: (_, fn) => (changed = fn),
    removeEventListener: vi.fn(),
    cancel: vi.fn(),
    speak: vi.fn((u) => u.onend()),
  };
  vi.stubGlobal("speechSynthesis", synthesis);
  vi.stubGlobal(
    "SpeechSynthesisUtterance",
    class {
      constructor(text) {
        this.text = text;
      }
    },
  );
  const result = speakLocally("우유를 넣으세요");
  expect(synthesis.speak).not.toHaveBeenCalled();
  voices = [basic];
  changed();
  await result;
  expect(synthesis.speak.mock.calls[0][0].rate).toBe(1);
  expect(synthesis.speak.mock.calls[0][0].voice).toBe(basic);
});
it("재생 중 중지하면 큐를 취소하고 실패 상태로 반환한다", async () => {
  vi.stubGlobal("speechSynthesis", {
    getVoices: () => [basic],
    speak: vi.fn(),
    cancel: vi.fn(),
  });
  vi.stubGlobal("SpeechSynthesisUtterance", class {});
  const controller = new AbortController();
  const result = speakLocally("답변", { signal: controller.signal });
  await Promise.resolve();
  controller.abort();
  expect(await result).toBe(false);
  expect(speechSynthesis.cancel).toHaveBeenCalledOnce();
});
