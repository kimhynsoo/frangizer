import { expect, it } from "vitest";
import { cleanAnswerText, spokenAnswer } from "../src/api/spoken-answer";
it("저장된 답변의 시행일 검수 표시는 숨기고 실제 적용 조건은 유지한다", () => {
  const answer =
    "[시행일 미검수] 인용 자료의 조문별 시행일 검수가 완료되지 않았습니다. 계약 전 정보공개서를 확인하세요. 적용 여부는 추가 확인이 필요합니다.";
  const expected =
    "계약 전 정보공개서를 확인하세요. 적용 여부는 추가 확인이 필요합니다.";
  expect(cleanAnswerText(answer)).toBe(expected);
  expect(spokenAnswer({ answer, status: "needs_review" })).toBe(expected);
});
it("반복 시연 안내와 내부 지시문을 빼고 제조 내용을 먼저 읽는다", () => {
  const answer =
    "합성 시연 레시피 기준입니다. 도구 사용은 종료하고, 확보한 근거 조각 ID만 사용해 최종 초안을 정리합니다. 바닐라 시럽 20 mL와 에스프레소 60 mL를 먼저 섞습니다.";
  const text = spokenAnswer({ answer, status: "answered" }, "어떻게 만들어?");
  expect(text).toBe(
    "바닐라 시럽 20 밀리리터와 에스프레소 60 밀리리터를 먼저 섞어 주세요.",
  );
  expect(cleanAnswerText(answer)).not.toContain("합성 시연 레시피 기준입니다.");
  expect(cleanAnswerText(answer)).not.toContain("도구 사용");
});
it("펌프 수 질문은 필요한 수치와 조건을 유지한다", () => {
  const text = spokenAnswer(
    {
      status: "answered",
      answer:
        "시럽과 에스프레소를 섞습니다. 기본은 4펌프입니다. 덜 달게는 3펌프입니다. 시럽 제외는 0펌프입니다. 우유를 넣습니다.",
    },
    "시럽 몇 펌프야?",
  );
  expect(text).toContain("4펌프");
  expect(text).toContain("3펌프");
  expect(text).toContain("0펌프");
  expect(text).not.toContain("우유");
});
it("제조 단계가 계량을 모두 담고 있으면 반복 설명 대신 단계를 읽는다", () => {
  const text = spokenAnswer(
    {
      status: "answered",
      answer: "시럽 20 mL와 우유 200 mL로 만듭니다.",
      steps: ["시럽 20 mL를 넣습니다.", "우유 200 mL를 붓습니다."],
    },
    "어떻게 만들어?",
  );
  expect(text).toBe(
    "시럽 20 밀리리터를 넣으세요. 우유 200 밀리리터를 부으세요.",
  );
});
it("단계에 계량이 빠져 있으면 본문의 수치를 유지한다", () => {
  const text = spokenAnswer(
    {
      status: "answered",
      answer: "HOT은 20 mL, ICED는 15 mL입니다.",
      steps: ["시럽과 에스프레소를 섞습니다."],
    },
    "만드는 순서",
  );
  expect(text).toContain("핫은 20 밀리리터");
  expect(text).toContain("아이스는 15 밀리리터");
});
it("근거 부족 답변을 제조 지시로 바꾸지 않는다", () => {
  const answer = "현재 시연에 등록된 자료로는 답을 확인할 수 없습니다.";
  expect(
    spokenAnswer(
      { status: "needs_review", answer, steps: ["시럽을 넣으세요"] },
      "몇 펌프야?",
    ),
  ).toBe(answer);
});
it("주의사항과 소수 계량을 보존한다", () => {
  const text = spokenAnswer(
    {
      status: "answered",
      answer:
        "합성 시연 레시피 기준입니다. 시럽은 2펌프입니다. 알레르기 성분을 반드시 확인하세요. 파우더는 1.5 g입니다.",
    },
    "몇 펌프야?",
  );
  expect(text).toContain("알레르기");
  expect(text).toContain("2펌프");
  expect(spokenAnswer({ answer: "파우더는 1.5 g입니다." })).toBe(
    "파우더는 1.5 그램이에요.",
  );
});
