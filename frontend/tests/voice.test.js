import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  voiceCommand,
  encodeWav,
  browserListen,
  recordAudio,
} from "../src/api/voice";
import { applicationStatus } from "../src/api/documents";
import { conversationMessages } from "../src/api/rag";
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
it("명시적인 보내기만 확인 명령으로 처리하고 부정·질문 문장은 보내지 않는다", () => {
  for (const value of ["보내기", "질문 보내기.", "전송해"])
    expect(voiceCommand(value)).toBe("send");
  for (const value of [
    "안 보내기",
    "보내지 마",
    "시럽 몇 펌프야?",
    "다시 보내기",
  ])
    expect(voiceCommand(value)).toBeNull();
  expect(voiceCommand("취소")).toBe("cancel");
  expect(voiceCommand("다시 말하기")).toBe("retry");
});
it("WAV는 실제 PCM 헤더·길이·샘플을 가진다", async () => {
  const blob = encodeWav([new Float32Array([-1, 0, 1])], 16000),
    data = new DataView(await blob.arrayBuffer());
  expect(blob.type).toBe("audio/wav");
  expect(blob.size).toBe(50);
  expect(data.getUint32(24, true)).toBe(16000);
  expect(data.getUint16(22, true)).toBe(1);
  expect(data.getInt16(44, true)).toBe(-32768);
  expect(data.getInt16(48, true)).toBe(32767);
});
it("브라우저 인식은 실제 결과만 반환하고 취소 시 엔진을 중단한다", async () => {
  let instance;
  class Recognition {
    constructor() {
      instance = this;
    }
    start() {}
    stop() {
      this.onend?.();
    }
    abort() {
      this.aborted = true;
    }
  }
  vi.stubGlobal("SpeechRecognition", Recognition);
  const promise = browserListen();
  instance.onresult({
    results: [[{ transcript: "바닐라라테 시럽 몇 펌프야" }]],
  });
  instance.onend();
  expect(await promise).toBe("바닐라라테 시럽 몇 펌프야");
  const controller = new AbortController(),
    cancelled = browserListen({ signal: controller.signal }).catch((e) => e);
  controller.abort();
  expect((await cancelled).name).toBe("AbortError");
  expect(instance.aborted).toBe(true);
});
it("마이크 권한 요청 중 취소해도 나중에 열린 마이크를 즉시 닫는다", async () => {
  let resolve;
  const stop = vi.fn();
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("navigator", {
    mediaDevices: { getUserMedia: () => new Promise((r) => (resolve = r)) },
  });
  const controller = new AbortController(),
    result = recordAudio({ signal: controller.signal }).catch((e) => e);
  controller.abort();
  resolve({ getTracks: () => [{ stop }] });
  expect((await result).name).toBe("AbortError");
  expect(stop).toHaveBeenCalledOnce();
});
it("말하기와 침묵이 감지되면 녹음을 끝내고 트랙·AudioContext를 정리한다", async () => {
  const stop = vi.fn(),
    close = vi.fn().mockResolvedValue(),
    disconnect = vi.fn(),
    startTime = Date.now();
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("navigator", {
    mediaDevices: {
      getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] }),
    },
  });
  class Recorder {
    static isTypeSupported() {
      return true;
    }
    state = "inactive";
    constructor() {
      this.state = "inactive";
    }
    start() {
      this.state = "recording";
    }
    stop() {
      this.state = "inactive";
      this.ondataavailable?.({
        data: new Blob(["real-recording-bytes"], { type: "audio/webm" }),
      });
      this.onstop?.();
    }
  }
  class Context {
    sampleRate = 48000;
    resume = vi.fn().mockResolvedValue();
    close = close;
    createMediaStreamSource() {
      return { connect: vi.fn(), disconnect };
    }
    createAnalyser() {
      return {
        fftSize: 2048,
        getFloatTimeDomainData(buffer) {
          const elapsed = Date.now() - startTime;
          buffer.fill(elapsed >= 500 && elapsed < 1000 ? 0.1 : 0.001);
        },
      };
    }
  }
  vi.stubGlobal("MediaRecorder", Recorder);
  vi.stubGlobal("AudioContext", Context);
  const outcome = recordAudio();
  await vi.advanceTimersByTimeAsync(3000);
  const blob = await outcome;
  expect(blob.type).toContain("webm");
  expect(blob.size).toBeGreaterThan(0);
  expect(stop).toHaveBeenCalledOnce();
  expect(close).toHaveBeenCalledOnce();
  expect(disconnect).toHaveBeenCalled();
});
it("처리 중·실패 질문도 대화 복원에서 보존한다", () => {
  const result = conversationMessages([
    {
      question_id: "q1",
      question_redacted: "질문1",
      answer_status: "processing",
      result: null,
      error_code: null,
    },
    {
      question_id: "q2",
      question_redacted: "질문2",
      answer_status: "system_error",
      result: null,
      error_code: "UPSTREAM_TIMEOUT",
    },
  ]);
  expect(result).toHaveLength(2);
  expect(result[1].errorCode).toBe("UPSTREAM_TIMEOUT");
});
it("배포됐어도 미래 판본은 예정으로, 현재 판본은 서버 선택 결과로 구분한다", () => {
  const current = {
      document_id: "doc",
      version_id: "v1",
      lifecycle: "published",
      effective_from: "2026-10-01T00:00:00+09:00",
    },
    future = {
      ...current,
      version_id: "v2",
      effective_from: "2026-10-26T00:00:00+09:00",
    },
    now = new Date("2026-10-23T00:00:00+09:00").valueOf();
  expect(applicationStatus(current, "v1", now)).toBe("current");
  expect(applicationStatus(future, "v1", now)).toBe("scheduled");
  expect(applicationStatus(current, undefined, now)).toBe(
    "application_unknown",
  );
  expect(applicationStatus({ ...current, lifecycle: "draft" }, "v1", now)).toBe(
    "not_applied",
  );
});
