// Fixed FAQ copy. Replace each placeholder only after its answer is verified.
const unavailableAnswer =
  "해당 기준 시점에 유효한 자료로는 답을 확인할 수 없습니다. 본사 담당자에게 문의해 주세요.";
export const faqItems = [
  {
    id: "getting-started",
    category: "가맹 준비",
    question: "가맹을 준비하려면 무엇부터 확인하나요?",
  },
  {
    id: "required-documents",
    category: "가맹 준비",
    question: "계약 전에 받아서 확인해야 하는 자료는 무엇인가요?",
  },
  {
    id: "opening-support",
    category: "가맹 준비",
    question: "상권·입지 선정과 개점 교육은 어떻게 지원하나요?",
  },
  {
    id: "total-cost",
    category: "창업 비용",
    question: "창업에 필요한 총비용은 얼마인가요?",
  },
  {
    id: "fees",
    category: "창업 비용",
    question: "가맹비·교육비·보증금은 각각 얼마인가요?",
  },
  {
    id: "extra-equipment",
    category: "구매·계약 조건",
    question: "추가 장비 구매 요구의 근거는 무엇인가요?",
  },
  {
    id: "required-purchases",
    category: "구매·계약 조건",
    question: "필수 구매 품목과 지정 거래처가 있나요?",
  },
  {
    id: "renewal",
    category: "구매·계약 조건",
    question: "계약 기간과 갱신 조건은 어떻게 되나요?",
  },
  {
    id: "termination",
    category: "구매·계약 조건",
    question: "계약 해지 조건과 위약금 기준은 무엇인가요?",
  },
].map((item) => ({ ...item, answer: unavailableAnswer }));
