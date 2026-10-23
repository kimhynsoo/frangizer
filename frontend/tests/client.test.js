import { afterEach, beforeEach, expect, it, vi } from "vitest";
let clientModule;
beforeEach(async () => {
  vi.useFakeTimers();
  vi.resetModules();
  const values = new Map();
  vi.stubGlobal("sessionStorage", {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  });
  vi.stubGlobal("window", new EventTarget());
  clientModule = await import("../src/api/client.js");
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
async function settle(promise) {
  const outcome = promise.then(
    (value) => ({ value }),
    (error) => ({ error }),
  );
  await vi.advanceTimersByTimeAsync(400);
  const result = await outcome;
  if (result.error) throw result.error;
  return result.value;
}
it("공통 클라이언트와 화면용 API 모듈이 토큰과 재시도 키를 전달한다", async () => {
  clientModule.tokenStorage.set("synthetic-owner");
  const api = await import("../src/api/rag.js");
  const key = crypto.randomUUID();
  const payload = { question: "바닐라라테 계량을 알려주세요." };
  const first = await settle(api.ask(payload, key));
  const retry = await settle(api.ask(payload, key));
  expect(first.data.question_id).toBe(retry.data.question_id);
  expect(first.data.status).toBe("answered");
});
it("401은 토큰을 비우고 재로그인 이벤트를 전달한다", async () => {
  const listener = vi.fn();
  window.addEventListener("session-expired", listener);
  const result = settle(clientModule.request({ url: "/me" })).catch(
    (error) => error,
  );
  const error = await result;
  expect(error.response.status).toBe(401);
  expect(clientModule.tokenStorage.get()).toBeNull();
  expect(listener).toHaveBeenCalledOnce();
  expect(error.message).toBe("다시 로그인해 주세요.");
});
it("다운로드에 외부 주소를 주어도 Bearer 토큰을 전송하지 않는다", async () => {
  const request = vi.spyOn(clientModule.client, "get");
  await expect(
    clientModule.downloadFile("https://external.invalid/file", "file.pdf"),
  ).rejects.toThrow("허용된 문서");
  expect(request).not.toHaveBeenCalled();
});
