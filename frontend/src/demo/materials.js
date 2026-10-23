import snapshot from "./synthetic-materials.json";

export const recipeMaterials = snapshot.recipes;
export const recipe10 = recipeMaterials.find(
  (item) => item.document.version === "1.0",
);
export const recipe11 = recipeMaterials.find(
  (item) => item.document.version === "1.1",
);
export function materialFor(version) {
  return recipeMaterials.find(
    (item) =>
      item.document.document_id === version.document_id &&
      item.document.version === version.version &&
      item.document.sha256 === version.sha256 &&
      version.material_type === "synthetic" &&
      version.is_demo === true &&
      version.category === "recipe" &&
      new Date(item.document.effective_from).valueOf() ===
        new Date(version.effective_from).valueOf(),
  );
}
export function sectionsFor(version) {
  return materialFor(version)?.sections || [];
}
export function uploadPreset() {
  const version = recipe11.document;
  return {
    title: version.title,
    version: version.version,
    document_id: version.document_id,
    category: version.category,
    material_type: version.material_type,
    visibility: version.visibility,
    effective_from: "2026-10-26T00:00",
    change_summary: version.change_summary,
    publisher: version.original_source.publisher,
    source_title: version.original_source.title,
    source_url: "",
    edition: version.original_source.edition,
    acquired_on: version.original_source.acquired_on,
    note: version.original_source.note,
    include_pages: "",
    exclude_pages: "",
    legal_temporal_reviewed: false,
  };
}
// Only these PDF-reviewed default recipes are simulated. Other questions need real RAG.
export function recipeContent(version, menu, hot = false) {
  if (!materialFor(version)) return null;
  if (menu === "honey_grapefruit_tea") {
    if (version.version !== "1.1" || hot) return null;
    return {
      page: 9,
      heading: "허니자몽블랙티 ICED",
      summary:
        "허니자몽블랙티는 미리 준비한 홍차 150 mL, 자몽 베이스 60 g, 꿀 10 g, 얼음 160 g을 사용합니다.",
      steps: [
        "별도 용기에서 홍차 150 mL, 자몽 베이스 60 g, 꿀 10 g을 섞습니다.",
        "ICED 컵에 얼음 160 g을 넣습니다.",
        "홍차 혼합액을 붓습니다.",
      ],
      excerpt: "별도 용기에서 홍차 150 mL, 자몽 베이스 60 g, 꿀 10 g을 섞는다.",
    };
  }
  if (menu !== "vanilla_latte") return null;
  const syrup = version.version === "1.0" ? 20 : 15;
  return {
    page: 5,
    heading: `바닐라라테 ${hot ? "HOT" : "ICED"}`,
    summary: `${hot ? "HOT" : "ICED"} 바닐라라테의 기본 바닐라 시럽은 ${syrup} mL, 전용 펌프 ${syrup / 5}회입니다.`,
    steps: hot
      ? [
          `HOT 컵에 바닐라 시럽 ${syrup} mL와 에스프레소 60 mL를 넣어 섞습니다.`,
          "계량한 우유 210 mL를 스팀하고 컵에 붓습니다.",
        ]
      : [
          `바닐라 시럽 ${syrup} mL와 에스프레소 60 mL를 별도 용기에서 섞습니다.`,
          "ICED 컵에 우유 170 mL와 얼음 180 g을 넣습니다.",
          "시럽과 에스프레소 혼합액을 붓습니다.",
        ],
    excerpt: hot
      ? `HOT 컵에 바닐라 시럽 ${syrup} mL와 에스프레소 60 mL를 넣어 섞는다.`
      : `바닐라 시럽 ${syrup} mL와 에스프레소 60 mL를 별도 용기에서 섞는다.`,
  };
}
