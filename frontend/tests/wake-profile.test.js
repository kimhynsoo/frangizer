import { expect, it } from "vitest";
import {
  clearWakeProfile,
  loadWakeProfile,
  normalizeWakeAlias,
  resetLegacyWakeProfiles,
  saveWakeProfile,
} from "../src/api/wake-profile";
import { wakeQuestion } from "../src/api/hands-free";
function storage() {
  const values = new Map();
  return {
    get length() {
      return values.size;
    },
    key: (index) => [...values.keys()][index] ?? null,
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}
it("확인한 표현만 정리·중복 제거하고 계정과 시연 모드를 분리한다", () => {
  const s = storage();
  saveWakeProfile("live:owner", ["쉬 리", "쉬리", "씨리"], s);
  expect(loadWakeProfile("live:owner", s)).toEqual(["쉬리", "씨리"]);
  expect(loadWakeProfile("live:hq", s)).toEqual([]);
  expect(loadWakeProfile("demo:owner", s)).toEqual([]);
  saveWakeProfile("live:hq", ["씨리"], s);
  clearWakeProfile("live:owner", s);
  expect(loadWakeProfile("live:owner", s)).toEqual([]);
  expect(loadWakeProfile("live:hq", s)).toEqual(["씨리"]);
});
it("등록된 발음만 문장 앞 호출어로 사용하고 나머지 질문을 보존한다", () => {
  expect(wakeQuestion("쉬 리 바닐라라테 어떻게 만들어", ["쉬리"])).toBe(
    "바닐라라테 어떻게 만들어",
  );
  expect(wakeQuestion("쉬리", ["쉬리"])).toBe("");
  expect(wakeQuestion("쉬리")).toBeNull();
  expect(wakeQuestion("이건 쉬리", ["쉬리"])).toBeNull();
});
it("등록할 때 인사·공백·구두점을 정리한다", () => {
  expect(normalizeWakeAlias("헤이 씨 리야!")).toBe("씨리");
});
it("빈 계정·빈 선택·쓸 수 없는 표현은 저장하지 않는다", () => {
  const s = storage();
  expect(() => saveWakeProfile("", ["씨리"], s)).toThrow();
  expect(() => saveWakeProfile("live:owner", [], s)).toThrow();
  expect(() => saveWakeProfile("live:owner", [".*"], s)).toThrow();
});
it("브라우저 저장 실패를 성공으로 표시하지 않는다", () => {
  const s = {
    setItem: () => {
      throw new Error("quota");
    },
  };
  expect(() => saveWakeProfile("live:owner", ["씨리"], s)).toThrow(
    "저장하지 못했습니다",
  );
});

it("옛 호출어 등록은 모든 계정에서 초기화하고 로그인·대화는 보존한다", () => {
  const s = storage();
  s.setItem(
    "frangizer.wake-aliases.v1.live%3Aowner",
    JSON.stringify({ aliases: ["프랜차이즈"] }),
  );
  s.setItem(
    "frangizer.wake-aliases.v1.live%3Ahq",
    JSON.stringify({ aliases: ["브란자이즈"] }),
  );
  s.setItem("frangizer.live.token", "existing-token");
  s.setItem("frangizer.chats", "existing-chats");
  expect(resetLegacyWakeProfiles(s)).toBe(2);
  expect(loadWakeProfile("live:owner", s)).toEqual([]);
  expect(s.getItem("frangizer.live.token")).toBe("existing-token");
  expect(s.getItem("frangizer.chats")).toBe("existing-chats");
  expect(resetLegacyWakeProfiles(s)).toBe(0);
  saveWakeProfile("live:owner", ["시리"], s);
  expect(loadWakeProfile("live:owner", s)).toEqual(["시리"]);
  expect(resetLegacyWakeProfiles(s)).toBe(0);
});
it("이전 개인 발음이 전달돼도 옛 호출어에는 반응하지 않는다", () => {
  expect(wakeQuestion("프랜자이저", ["프랜자이저"])).toBeNull();
  expect(wakeQuestion("프랜차이즈 시럽 몇 펌프", ["프랜차이즈"])).toBeNull();
});
