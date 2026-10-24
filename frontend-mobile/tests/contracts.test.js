import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { parse } from "yaml";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { AxiosHeaders } from "axios";

const spec = parse(
  readFileSync(
    new URL("../../docs/api/public.openapi.yaml", import.meta.url),
    "utf8",
  ),
);
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const validators = new Map();
let adapter;
const token = (role) => `Bearer synthetic-${role}`;
async function call(
  method,
  path,
  { role = "owner", data, params, key, contractPath = path } = {},
) {
  const promise = adapter({
    method,
    url: path,
    data,
    params,
    headers: new AxiosHeaders({
      Authorization: token(role),
      ...(key ? { "Idempotency-Key": key } : {}),
    }),
  });
  // Attach a rejection handler before advancing the asynchronous demo transport.
  const outcome = promise.then(
    (value) => ({ value }),
    (error) => ({ error }),
  );
  await vi.advanceTimersByTimeAsync(400);
  const { value, error } = await outcome;
  if (error) throw error;
  const operation = spec.paths[contractPath]?.[method];
  const schema =
    operation?.responses[String(value.status)]?.content?.["application/json"]
      ?.schema;
  if (schema) {
    const name = `${method}:${contractPath}:${value.status}`;
    if (!validators.has(name))
      validators.set(
        name,
        ajv.compile({ components: spec.components, ...schema }),
      );
    const validate = validators.get(name);
    expect(validate(value.data), JSON.stringify(validate.errors)).toBe(true);
  }
  return value.data;
}
beforeEach(async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-23T03:00:00Z"));
  vi.resetModules();
  adapter = (await import("../src/demo/mock.js")).mockAdapter;
});
afterEach(() => vi.useRealTimers());

describe("공개 API 계약과 시연 연결", () => {
  it("로그인과 내 정보 응답이 역할·권한 계약을 만족한다", async () => {
    for (const role of ["owner", "prospect", "hq"]) {
      const username = role === "owner" ? "demo-owner-a001" : `demo-${role}`;
      const login = await call("post", "/login", {
        data: JSON.stringify({ username, password: "demo-password" }),
      });
      expect(login.user.role).toBe(role);
      expect((await call("get", "/me", { role })).data.role).toBe(role);
    }
  });
  it("같은 질문 키의 재시도는 질문을 중복 생성하지 않는다", async () => {
    const data = { question: "아이스 바닐라라테 제조 순서 알려주세요." },
      key = crypto.randomUUID();
    const first = await call("post", "/rag-query", { data, key });
    const retried = await call("post", "/rag-query", { data, key });
    expect(first.data.question_id).toBe(retried.data.question_id);
    expect(first.data.sources[0].location).toContain("PDF 파일 5쪽 (인쇄 6쪽)");
    expect(first.data.steps).toHaveLength(3);
    const history = await call(
      "get",
      `/conversations/${first.data.conversation_id}`,
      { contractPath: "/conversations/{conversation_id}" },
    );
    expect(history.data.messages).toHaveLength(1);
    await expect(
      call("post", "/rag-query", { data: { question: "바뀐 질문" }, key }),
    ).rejects.toMatchObject({ response: { status: 409 } });
  });
  it("근거 부족·담당자 검토를 성공 답변과 구분하고 무근거 제조 수치를 제시하지 않는다", async () => {
    for (const [question, status] of [
      ["미등록 메뉴 알려줘", "needs_review"],
      ["이물 발견 환불을 해줘", "handoff"],
    ]) {
      const result = (
        await call("post", "/rag-query", {
          data: { question },
          key: crypto.randomUUID(),
        })
      ).data;
      expect(result.status).toBe(status);
      expect(result.sources).toEqual([]);
      expect(result.steps).toEqual([]);
    }
    const prospect = (
      await call("post", "/rag-query", {
        role: "prospect",
        data: { question: "바닐라라테 계량" },
        key: crypto.randomUUID(),
      })
    ).data;
    expect(prospect.sources).toEqual([]);
  });
  it("피드백은 별도 계약으로 전송한다", async () => {
    const answer = (
      await call("post", "/rag-query", {
        data: { question: "바닐라라테 시럽" },
        key: crypto.randomUUID(),
      })
    ).data;
    const result = await call(
      "put",
      `/questions/${answer.question_id}/feedback`,
      {
        data: { helpful: true },
        contractPath: "/questions/{question_id}/feedback",
      },
    );
    expect(result.data.helpful).toBe(true);
  });
  it("문서 목록·판본·본문·원본 다운로드 흐름을 제공한다", async () => {
    const doc = (await call("get", "/documents")).data[0];
    expect(doc.title).toBe("카페 표준 레시피 북 (합성)");
    expect(doc.effective_from).toBe("2026-10-01T00:00:00+09:00");
    const adminHistory = await call(
      "get",
      `/documents/${doc.document_id}/versions`,
      { role: "hq", contractPath: "/documents/{doc_id}/versions" },
    );
    expect(adminHistory.data.map((item) => item.version)).toEqual(["1.0"]);
    expect(adminHistory.data[0].page_count).toBe(8);
    expect(adminHistory.data[0].indexing_policy.exclude_pages).toEqual([]);
    const history = await call(
      "get",
      `/documents/${doc.document_id}/versions`,
      { contractPath: "/documents/{doc_id}/versions" },
    );
    expect(history.data.every((v) => v.lifecycle === "published")).toBe(true);
    await call(
      "get",
      `/documents/${doc.document_id}/versions/${doc.selected_version_id}`,
      { contractPath: "/documents/{doc_id}/versions/{ver_id}" },
    );
    await expect(
      call(
        "get",
        "/documents/demo-cafe-recipes/versions/demo-cafe-recipes-1.0",
        {
          role: "prospect",
          contractPath: "/documents/{doc_id}/versions/{ver_id}",
        },
      ),
    ).rejects.toMatchObject({ response: { status: 403 } });
  });
  it("등록 → 승인 → 검색 준비 → 배포를 검증하며 미래 판본을 현재 답변에 섞지 않는다", async () => {
    const reviewed = JSON.parse(
      readFileSync(
        new URL("../src/demo/synthetic-materials.json", import.meta.url),
        "utf8",
      ),
    ).recipes.find((item) => item.document.version === "1.1");
    const document = reviewed.document;
    const metadata = Object.fromEntries(
      [
        "title",
        "version",
        "document_id",
        "category",
        "material_type",
        "is_demo",
        "visibility",
        "effective_from",
        "change_summary",
        "original_source",
        "indexing_policy",
      ].map((key) => [key, document[key]]),
    );
    const form = new FormData();
    form.append("metadata", JSON.stringify(metadata));
    form.append(
      "file",
      new Blob(
        [
          readFileSync(
            new URL(`../../${reviewed.source_file}`, import.meta.url),
          ),
        ],
        { type: "application/pdf" },
      ),
      document.file_name,
    );
    const before = (
      await call("post", "/rag-query", {
        data: {
          question: "바닐라라테 시럽",
          as_of: "2026-10-26T00:00:00+09:00",
        },
        key: crypto.randomUUID(),
      })
    ).data;
    expect(before.status).toBe("needs_review");
    const uploaded = (
      await call("post", "/documents", {
        role: "hq",
        data: form,
        key: crypto.randomUUID(),
      })
    ).data;
    const prefix = `/documents/${uploaded.document_id}/versions/${uploaded.version_id}`;
    expect(uploaded.page_count).toBe(10);
    expect(uploaded.sha256).toBe(document.sha256);
    const draftAnswer = (
      await call("post", "/rag-query", {
        data: {
          question: "허니자몽블랙티 레시피",
          as_of: document.effective_from,
        },
        key: crypto.randomUUID(),
      })
    ).data;
    expect(draftAnswer.status).toBe("needs_review");
    await expect(
      call("post", `${prefix}/publish`, {
        role: "hq",
        data: { notice_title: "미래 레시피" },
        key: crypto.randomUUID(),
        contractPath: "/documents/{doc_id}/versions/{ver_id}/publish",
      }),
    ).rejects.toMatchObject({ response: { status: 409 } });
    const approved = (
      await call("post", `${prefix}/approve`, {
        role: "hq",
        data: { review_note: "검수 완료" },
        key: crypto.randomUUID(),
        contractPath: "/documents/{doc_id}/versions/{ver_id}/approve",
      })
    ).data;
    await vi.advanceTimersByTimeAsync(3000);
    const job = (
      await call("get", `/index-jobs/${approved.job_id}`, {
        role: "hq",
        contractPath: "/index-jobs/{job_id}",
      })
    ).data;
    expect(job.status).toBe("succeeded");
    await call("post", `${prefix}/publish`, {
      role: "hq",
      data: { notice_title: "미래 레시피" },
      key: crypto.randomUUID(),
      contractPath: "/documents/{doc_id}/versions/{ver_id}/publish",
    });
    const current = (
      await call("post", "/rag-query", {
        data: { question: "바닐라라테 시럽" },
        key: crypto.randomUUID(),
      })
    ).data;
    expect(current.sources[0].version).toBe("1.0");
    const future = (
      await call("post", "/rag-query", {
        data: {
          question: "바닐라라테 시럽",
          as_of: "2026-10-26T00:00:00+09:00",
        },
        key: crypto.randomUUID(),
      })
    ).data;
    expect(future.basis).toBe("future");
    expect(future.sources[0].version).toBe("1.1");
    expect(future.answer).toContain("15 mL");
    const beforeBoundary = (
      await call("post", "/rag-query", {
        data: {
          question: "바닐라라테 시럽",
          as_of: "2026-10-25T23:59:59+09:00",
        },
        key: crypto.randomUUID(),
      })
    ).data;
    expect(beforeBoundary.sources[0].version).toBe("1.0");
    expect(beforeBoundary.sources[0].effective_until).toBe(
      "2026-10-26T00:00:00+09:00",
    );
    const tea = (
      await call("post", "/rag-query", {
        data: {
          question: "허니자몽블랙티 제조 순서",
          as_of: document.effective_from,
        },
        key: crypto.randomUUID(),
      })
    ).data;
    expect(tea.sources[0].page).toBe(9);
    expect(tea.sources[0].location).toContain("인쇄 10쪽");
    expect(tea.answer).toContain("홍차 150 mL");
  });
  it("검토하지 않은 파일이나 다른 메뉴에 바닐라라테 수치를 대입하지 않는다", async () => {
    for (const question of [
      "말차라테 레시피",
      "바닐라라테 덜 달게",
      "허니자몽블랙티 제조 순서",
    ]) {
      const answer = (
        await call("post", "/rag-query", {
          data: { question },
          key: crypto.randomUUID(),
        })
      ).data;
      expect(answer.status).toBe("needs_review");
      expect(answer.sources).toEqual([]);
      expect(answer.steps).toEqual([]);
    }
    const invalidDate = (
      await call("post", "/rag-query", {
        data: {
          question: "바닐라라테 시럽",
          as_of: "2026-09-30T23:59:59+09:00",
        },
        key: crypto.randomUUID(),
      })
    ).data;
    expect(invalidDate.status).toBe("needs_review");
  });
  it("이름만 같은 다른 업로드 파일에서 검토된 레시피를 만들어내지 않는다", async () => {
    const reviewed = JSON.parse(
      readFileSync(
        new URL("../src/demo/synthetic-materials.json", import.meta.url),
        "utf8",
      ),
    ).recipes.find((item) => item.document.version === "1.1");
    const doc = reviewed.document;
    const metadata = Object.fromEntries(
      [
        "title",
        "version",
        "document_id",
        "category",
        "material_type",
        "is_demo",
        "visibility",
        "effective_from",
        "change_summary",
        "original_source",
        "indexing_policy",
      ].map((key) => [key, doc[key]]),
    );
    const form = new FormData();
    form.append("metadata", JSON.stringify(metadata));
    form.append(
      "file",
      new Blob(["검토되지 않은 내용"], { type: "application/pdf" }),
      doc.file_name,
    );
    const uploaded = (
      await call("post", "/documents", {
        role: "hq",
        data: form,
        key: crypto.randomUUID(),
      })
    ).data;
    expect(uploaded.sha256).not.toBe(doc.sha256);
    const base = `/documents/${uploaded.document_id}/versions/${uploaded.version_id}`;
    const approved = (
      await call("post", `${base}/approve`, {
        role: "hq",
        data: { review_note: "흐름만 시연" },
        key: crypto.randomUUID(),
        contractPath: "/documents/{doc_id}/versions/{ver_id}/approve",
      })
    ).data;
    await vi.advanceTimersByTimeAsync(3000);
    await call("get", `/index-jobs/${approved.job_id}`, {
      role: "hq",
      contractPath: "/index-jobs/{job_id}",
    });
    await call("post", `${base}/publish`, {
      role: "hq",
      data: { notice_title: "검토되지 않은 자료의 시연 배포" },
      key: crypto.randomUUID(),
      contractPath: "/documents/{doc_id}/versions/{ver_id}/publish",
    });
    const result = (
      await call("post", "/rag-query", {
        data: { question: "바닐라라테 시럽", as_of: doc.effective_from },
        key: crypto.randomUUID(),
      })
    ).data;
    expect(result.status).toBe("needs_review");
    expect(result.sources).toEqual([]);
  });
  it("권한 없는 본사 관리 접근을 거부한다", async () => {
    await expect(
      call("get", "/documents", { params: { management: true } }),
    ).rejects.toMatchObject({ response: { status: 403 } });
    await expect(call("get", "/analytics/questions")).rejects.toMatchObject({
      response: { status: 403 },
    });
    await expect(
      call("get", "/notices", { role: "prospect" }),
    ).rejects.toMatchObject({ response: { status: 403 } });
  });
  it("공지 확인은 점포당 한 번 기록하고 전체 확인 집계와 필터 목록을 구분한다", async () => {
    const notice = (await call("get", "/notices")).data[0],
      nid = notice.notice_id;
    const before = await call("get", `/notices/${nid}/acknowledgements`, {
      role: "hq",
      contractPath: "/notices/{notice_id}/acknowledgements",
    });
    const first = await call("put", `/notices/${nid}/ack`, {
      data: {},
      contractPath: "/notices/{notice_id}/ack",
    });
    const again = await call("put", `/notices/${nid}/ack`, {
      data: {},
      contractPath: "/notices/{notice_id}/ack",
    });
    expect(first.data.acknowledged_at).toBe(again.data.acknowledged_at);
    const after = await call("get", `/notices/${nid}/acknowledgements`, {
      role: "hq",
      params: { acknowledged: false },
      contractPath: "/notices/{notice_id}/acknowledgements",
    });
    expect(after.summary.acknowledged_count).toBe(
      before.summary.acknowledged_count + 1,
    );
    expect(after.data.every((r) => r.acknowledged_at === null)).toBe(true);
  });
  it("본사 집계·필터 선택지·문의 상세·동시 수정 충돌을 검증한다", async () => {
    for (const kind of ["regions", "stores", "topics"])
      await call("get", `/${kind}`, { role: "hq" });
    await call("get", "/analytics/questions", { role: "hq" });
    const row = (
      await call("get", "/analytics/question-events", { role: "hq" })
    ).data[0];
    await call("get", `/analytics/question-events/${row.question_id}`, {
      role: "hq",
      contractPath: "/analytics/question-events/{question_id}",
    });
    const updated = await call(
      "patch",
      `/analytics/question-events/${row.question_id}`,
      {
        role: "hq",
        data: {
          expected_revision: row.revision,
          resolution_status: "in_progress",
        },
        contractPath: "/analytics/question-events/{question_id}",
      },
    );
    expect(updated.data.revision).toBe(row.revision + 1);
    await expect(
      call("patch", `/analytics/question-events/${row.question_id}`, {
        role: "hq",
        data: { expected_revision: row.revision, resolution_status: "closed" },
        contractPath: "/analytics/question-events/{question_id}",
      }),
    ).rejects.toMatchObject({ response: { status: 409 } });
  });
});
