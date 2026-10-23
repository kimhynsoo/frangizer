import { afterEach, beforeEach, expect, it, vi } from "vitest";
beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal("sessionStorage", {
    getItem: () => null,
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});
it("실제 모드는 /stt로 audio 파트만 보내고 서버가 인식한 문장을 받는다", async () => {
  vi.stubEnv("VITE_USE_MOCK", "false");
  const { client } = await import("../src/api/client");
  const { transcribe } = await import("../src/api/stt");
  let captured;
  client.defaults.adapter = async (config) => {
    captured = config;
    return {
      config,
      status: 200,
      headers: {},
      data: {
        data: { text: "  바닐라라테 시럽 몇 펌프예요?  ", duration_ms: 2400 },
        request_id: "req-stt",
      },
    };
  };
  const controller = new AbortController(),
    blob = new Blob(["captured audio"], { type: "audio/webm;codecs=opus" });
  const response = await transcribe(blob, { signal: controller.signal });
  expect(captured.url).toBe("/stt");
  expect([...captured.data.keys()]).toEqual(["audio"]);
  expect(captured.data.get("audio").name).toBe("question.webm");
  expect(captured.signal).toBe(controller.signal);
  expect(response.text).toBe("바닐라라테 시럽 몇 펌프예요?");
});
it("지원하지 않는 음성 형식과 빈 음성을 전송하지 않는다", async () => {
  vi.stubEnv("VITE_USE_MOCK", "false");
  const { client } = await import("../src/api/client"),
    { transcribe } = await import("../src/api/stt");
  const spy = vi.spyOn(client, "request");
  await expect(
    transcribe(new Blob(["audio"], { type: "audio/mp4" })),
  ).rejects.toThrow("WebM 또는 WAV");
  await expect(transcribe(new Blob([], { type: "audio/wav" }))).rejects.toThrow(
    "10MB 이하",
  );
  expect(spy).not.toHaveBeenCalled();
});
it("샘플 모드에서 실제 녹음에 가짜 인식 문장을 반환하지 않는다", async () => {
  vi.stubEnv("VITE_USE_MOCK", "true");
  const { transcribe } = await import("../src/api/stt");
  await expect(
    transcribe(new Blob(["real audio"], { type: "audio/wav" })),
  ).rejects.toThrow("샘플 모드에는 음성 서버가 없습니다");
});
