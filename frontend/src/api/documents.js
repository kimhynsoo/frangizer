import { request, mutation, downloadFile } from "./client";
const versionPath = (doc, version) =>
  `/documents/${encodeURIComponent(doc)}/versions/${encodeURIComponent(version)}`;
export const list = (params) => request({ url: "/documents", params });
export const versions = (doc, params) =>
  request({ url: `/documents/${encodeURIComponent(doc)}/versions`, params });
export const detail = (doc, ver, params) =>
  request({ url: versionPath(doc, ver), params });
export const upload = (file, metadata, key) => {
  const data = new FormData();
  data.append("file", file);
  data.append("metadata", JSON.stringify(metadata));
  return mutation("/documents", data, key);
};
export const approve = (doc, ver, review_note, key) =>
  mutation(`${versionPath(doc, ver)}/approve`, { review_note }, key);
export const publish = (doc, ver, notice_title, key) =>
  mutation(`${versionPath(doc, ver)}/publish`, { notice_title }, key);
export const job = (id) =>
  request({ url: `/index-jobs/${encodeURIComponent(id)}` });
export const download = (path, name) => downloadFile(path, name);
// The current version ID comes from the server's as_of selection, not file age.
export function applicationStatus(version, currentVersionId, now = Date.now()) {
  if (version.lifecycle !== "published") return "not_applied";
  const start = new Date(version.effective_from).valueOf();
  if (Number.isFinite(start) && start > now) return "scheduled";
  const id = version.version_id || version.selected_version_id;
  if (currentVersionId === id) return "current";
  if (currentVersionId === undefined) return "application_unknown";
  return "not_current";
}

// The browser validates syntax and overlap; actual PDF bounds remain server-side.
export function pagePolicy(includeText = "", excludeText = "") {
  const parse = (value, label) => {
    if (!value.trim()) return [];
    const values = value.split(",").map((item) => item.trim());
    if (
      values.some(
        (item) =>
          !/^[1-9][0-9]*$/.test(item) || !Number.isSafeInteger(Number(item)),
      )
    )
      throw new Error(
        `${label} 페이지는 1 이상의 정수를 쉼표로 구분해 입력하세요.`,
      );
    return [...new Set(values.map(Number))];
  };
  const include = parse(includeText, "포함"),
    exclude = parse(excludeText, "제외");
  if (include.some((page) => exclude.includes(page)))
    throw new Error("같은 페이지를 포함과 제외에 동시에 지정할 수 없습니다.");
  return {
    ...(include.length ? { include_pages: include } : {}),
    exclude_pages: exclude,
  };
}
export function indexJobFailure(job) {
  return {
    message: job.error_message || "검색 데이터 준비에 실패했습니다.",
    errorCode: job.error_code || null,
    jobId: job.job_id,
    nextAction:
      job.error_code === "CHECKSUM_MISMATCH"
        ? "등록한 원본 파일과 체크섬을 확인하도록 데이터·API 담당자에게 작업 ID를 전달해 주세요."
        : "문서·판본과 작업 ID, 오류 코드를 데이터·API 담당자에게 전달해 원인을 확인해 주세요. 자동으로 다시 승인하거나 배포하지 않습니다.",
    retryable: false,
  };
}
