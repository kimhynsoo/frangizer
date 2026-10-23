import { AxiosError, AxiosHeaders } from "axios";
import {
  recipe10,
  recipe11,
  materialFor,
  sectionsFor,
  recipeContent,
} from "./materials.js";

// Browser-only synthetic state. Nothing is sent to a backend or LLM in demo mode.
const clone = (value) => JSON.parse(JSON.stringify(value));
const now = () => new Date().toISOString();
const id = (prefix) => `${prefix}-${crypto.randomUUID()}`;
const initial = "2026-10-23T10:00:00+09:00";
const recipeId = "demo-cafe-recipes";
// Explicit mock lifecycle seed, not inferred from repository source/staged locations.
const version10 = clone(recipe10.document);
const versions = [version10];
const publishedEnds = new Map();
const validUntil = (version) =>
  publishedEnds.get(version.version_id) ||
  materialFor(version)?.effective_until ||
  null;
const files = new Map(),
  jobs = new Map(),
  cache = new Map(),
  feedback = new Map();
const regions = [
  { code: "SEOUL", name: "서울" },
  { code: "GYEONGGI", name: "경기" },
  { code: "CHUNGCHEONG", name: "충청" },
];
const stores = Array.from({ length: 12 }, (_, i) => ({
  id: `demo-store-${String(i + 1).padStart(2, "0")}`,
  name: `시연 ${i + 1}호점`,
  region_code: regions[i % 3].code,
  active: true,
}));
const topics = [
  {
    id: "vanilla_syrup",
    label: "바닐라라테 시럽 계량",
    category: "recipe",
    active: true,
  },
  { id: "opening", label: "가맹 준비", category: "franchise", active: true },
  { id: "refund", label: "고객 불만 대응", category: "consumer", active: true },
];
const notices = [
  {
    notice_id: "notice-demo-001",
    document_id: recipeId,
    version_id: version10.version_id,
    title: "합성 레시피 v1.0 운영 기준 안내",
    change_summary:
      "바닐라라테의 계량과 제조 순서를 확인해 주세요. 실제 브랜드 제조법이 아닌 합성 시연 자료입니다.",
    effective_from: version10.effective_from,
    published_at: initial,
    acknowledged_at: null,
    detail_url: `/api/v1/documents/${recipeId}/versions/${version10.version_id}`,
  },
];
const acknowledgements = new Map([
  [
    "notice-demo-001",
    new Map(
      stores.slice(1, 9).map((s) => [
        s.id,
        {
          notice_id: "notice-demo-001",
          store_id: s.id,
          acknowledged_by: `demo-user-${s.id}`,
          acknowledged_at: initial,
        },
      ]),
    ),
  ],
]);
function userFor(role) {
  return {
    id: `demo-user-${role}`,
    username: `demo-${role}${role === "owner" ? "-a001" : ""}`,
    role,
    brand_id: "brand-demo-a",
    store_id: role === "owner" ? stores[0].id : null,
    region_code: role === "owner" ? "SEOUL" : null,
    permissions:
      role === "hq"
        ? [
            "rag:ask",
            "document:write",
            "document:approve",
            "document:publish",
            "analytics:read",
            "analytics:write",
          ]
        : ["rag:ask"],
  };
}
function sourceFor(version, content) {
  const material = materialFor(version);
  const section = material?.sections.find((item) => item.page === content.page);
  const printed =
    content.page === 5 && version.version === "1.0"
      ? 6
      : content.page === 9
        ? 10
        : content.page;
  return {
    chunk_id: section.chunk_id,
    document_id: version.document_id,
    version_id: version.version_id,
    title: version.title,
    version: version.version,
    location: `PDF 파일 ${content.page}쪽 (인쇄 ${printed}쪽) / ${content.heading}`,
    page: content.page,
    article: null,
    effective_from: version.effective_from,
    effective_until: validUntil(version),
    material_type: version.material_type,
    is_demo: version.is_demo,
    excerpt: content.excerpt,
    detail_url: `/api/v1/documents/${version.document_id}/versions/${version.version_id}`,
    file_url: version.file_url,
  };
}
function resultFor(question, user, asOf = now(), conversationId = id("conv")) {
  const menu = /허니자몽/.test(question)
    ? "honey_grapefruit_tea"
    : /바닐라/.test(question)
      ? "vanilla_latte"
      : null;
  const handoff = /환불|배상|위법|안전|이물/.test(question);
  const isOption =
    /덜\s*달|빼기|빼줘|얼음\s*(적게|없)|샷\s*추가|대체|디카페인/.test(question);
  const candidates = versions
    .filter(
      (v) =>
        v.document_id === recipeId &&
        v.lifecycle === "published" &&
        v.index_status === "ready" &&
        new Date(v.effective_from) <= new Date(asOf) &&
        (!validUntil(v) || new Date(asOf) < new Date(validUntil(v))),
    )
    .sort((a, b) => new Date(a.effective_from) - new Date(b.effective_from));
  const version = candidates.at(-1);
  const content =
    version && user.role !== "prospect" && !isOption
      ? recipeContent(version, menu, /HOT|핫|뜨거운|따뜻/i.test(question))
      : null;
  const status = handoff ? "handoff" : content ? "answered" : "needs_review";
  return {
    question_id: id("q"),
    conversation_id: conversationId,
    answer:
      status === "answered"
        ? `합성 시연 레시피 v${version.version} 기준입니다. ${content.summary} 실제 브랜드 제조법이 아닙니다.`
        : handoff
          ? "이 상황은 사실관계와 내부 정책을 확인한 뒤 담당자가 검토해야 합니다."
          : "현재 시연에 등록·승인되어 있고 해당 기준 시점에 유효한 자료로는 답을 확인할 수 없습니다. 실제 검색 결과는 서버 연결 후 확인할 수 있습니다.",
    steps: status === "answered" ? content.steps : [],
    status,
    needs_review: status !== "answered",
    handoff: status === "handoff",
    sources: status === "answered" ? [sourceFor(version, content)] : [],
    follow_up_questions:
      status === "answered"
        ? []
        : [
            "해당 기준 시점에 적용되는 승인 문서와 구체적인 질문을 확인해 주세요.",
          ],
    handoff_reason:
      status === "handoff"
        ? "환불·보상·안전·법적 판단은 권한 있는 담당자의 검토가 필요합니다."
        : status === "needs_review"
          ? "시연에서 확인 가능한 유효 근거가 부족합니다."
          : null,
    category: handoff ? "consumer" : menu ? "recipe" : "franchise",
    topic_id:
      menu === "vanilla_latte"
        ? "vanilla_syrup"
        : handoff
          ? "refund"
          : menu
            ? null
            : "opening",
    classification_status: "classified",
    as_of: asOf,
    basis:
      new Date(asOf) > new Date()
        ? "future"
        : new Date(asOf).toDateString() === new Date().toDateString()
          ? "current"
          : "past",
    latency_ms: 640,
  };
}
function eventFor(question, user, result, created = now()) {
  const store = stores.find((s) => s.id === user.store_id);
  return {
    question_id: result.question_id,
    conversation_id: result.conversation_id,
    store_id: store?.id ?? null,
    store_name: store?.name ?? null,
    region_code: store?.region_code ?? null,
    region_name:
      regions.find((r) => r.code === store?.region_code)?.name ?? null,
    user_role: user.role,
    created_at: created,
    updated_at: created,
    question_redacted: question.replace(
      /01[016789][- ]?\d{3,4}[- ]?\d{4}/g,
      "[전화번호]",
    ),
    answer_status: result.status,
    resolution_status: result.status === "answered" ? "unknown" : "open",
    category: result.category,
    topic_id: result.topic_id,
    classification_status: "classified",
    classification_version: "synthetic-rules-1",
    result,
    error_code: null,
    attempt_count: 1,
    revision: 1,
  };
}
function previousMonday() {
  const date = new Date();
  date.setUTCHours(0, 0, 0, 0);
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7) - 7);
  return date.toISOString().slice(0, 10);
}
const monday = previousMonday();
const events = Array.from({ length: 18 }, (_, i) => {
  const owner = { ...userFor("owner"), store_id: stores[i % 12].id };
  const q =
    i % 5 === 0
      ? "이물 발견 고객 환불은 어떻게 하나요?"
      : i % 4 === 0
        ? "미등록 시즌 메뉴를 알려주세요."
        : "아이스 바닐라라테 시럽 계량을 알려주세요.";
  const created = new Date(`${monday}T09:00:00+09:00`);
  created.setDate(created.getDate() + (i % 7));
  return eventFor(q, owner, resultFor(q, owner), created.toISOString());
});
function fail(config, status, message, code = "FORBIDDEN", retryable = false) {
  const response = {
    status,
    statusText: "Demo error",
    headers: {},
    config,
    data: {
      error: { code, message, details: [], question_id: null, retryable },
      request_id: id("req"),
    },
  };
  throw new AxiosError(message, "ERR_BAD_RESPONSE", config, null, response);
}
function ensure(config, user, permission) {
  if (!user.permissions.includes(permission))
    fail(config, 403, "이 작업에 대한 권한이 없습니다.");
}
function paged(rows, query, extra = {}) {
  const page = Number(query.page || 1),
    size = Number(query.page_size || 20);
  return {
    data: clone(rows.slice((page - 1) * size, page * size)),
    meta: {
      page,
      page_size: size,
      total: rows.length,
      total_pages: Math.ceil(rows.length / size),
    },
    ...extra,
  };
}
function permitted(v, user) {
  return (
    user.role === "hq" ||
    (v.lifecycle === "published" &&
      (v.visibility === "prospect" ||
        (user.role === "owner" && v.visibility === "owner")))
  );
}
function filteredEvents(query) {
  let rows = events.filter((e) => e.user_role === (query.user_role || "owner"));
  const start = query.week_start
    ? `${query.week_start}T00:00:00+09:00`
    : query.from || `${monday}T00:00:00+09:00`;
  const end =
    query.to ||
    new Date(new Date(start).valueOf() + 7 * 86400000).toISOString();
  rows = rows.filter(
    (e) =>
      new Date(e.created_at) >= new Date(start) &&
      new Date(e.created_at) < new Date(end),
  );
  for (const key of [
    "region_code",
    "store_id",
    "category",
    "topic_id",
    "answer_status",
    "resolution_status",
    "classification_status",
  ])
    if (query[key]) rows = rows.filter((e) => e[key] === query[key]);
  return rows;
}
function counts(rows) {
  const total = rows.length,
    answered = rows.filter((e) => e.answer_status === "answered").length,
    review = rows.filter((e) => e.answer_status === "needs_review").length,
    handoff = rows.filter((e) => e.answer_status === "handoff").length,
    completed = answered + review + handoff,
    active = new Set(rows.map((e) => e.store_id).filter(Boolean)).size;
  return {
    total,
    processing_count: 0,
    answered_count: answered,
    review_required_count: review + handoff,
    needs_review_count: review,
    handoff_count: handoff,
    system_error_count: 0,
    completed_count: completed,
    active_store_count: active,
    classification_pending_count: 0,
    guidance_rate: completed ? answered / completed : null,
    review_required_rate: completed ? (review + handoff) / completed : null,
    questions_per_active_store: active ? total / active : null,
  };
}
export async function mockAdapter(config) {
  await new Promise((resolve) => setTimeout(resolve, 360));
  const path = config.url.replace(/^\/api\/v1/, "").split("?")[0],
    method = config.method.toLowerCase(),
    query = config.params || {};
  const body =
    typeof config.data === "string" ? JSON.parse(config.data) : config.data;
  const token =
    config.headers.get?.("Authorization") || config.headers.Authorization;
  let user = null;
  if (token?.startsWith("Bearer synthetic-"))
    user = userFor(token.slice("Bearer synthetic-".length));
  if (path !== "/login" && !user)
    fail(config, 401, "다시 로그인해 주세요.", "AUTH_REQUIRED");
  const key =
    config.headers.get?.("Idempotency-Key") ||
    config.headers["Idempotency-Key"];
  const cacheKey = `${user?.id}:${method}:${path}:${key}`;
  const fingerprint =
    body instanceof FormData
      ? `${body.get("metadata")}:${body.get("file")?.name}:${body.get("file")?.size}`
      : JSON.stringify(body);
  if (key && cache.has(cacheKey)) {
    const stored = cache.get(cacheKey);
    if (stored.fingerprint !== fingerprint)
      fail(
        config,
        409,
        "재시도 요청 내용이 변경됐습니다.",
        "IDEMPOTENCY_CONFLICT",
      );
    return cloneResponse(stored.response, config);
  }
  let payload,
    status = 200;
  if (path === "/login") {
    const role =
      body.username === "demo-owner-a001"
        ? "owner"
        : body.username === "demo-prospect"
          ? "prospect"
          : body.username === "demo-hq"
            ? "hq"
            : null;
    if (!role || body.password !== "demo-password")
      fail(config, 401, "아이디 또는 비밀번호를 확인해 주세요.", "AUTH_FAILED");
    payload = {
      accessToken: `synthetic-${role}`,
      token_type: "Bearer",
      expires_in: 3600,
      user: userFor(role),
    };
  } else if (path === "/me") payload = { data: user };
  else if (path === "/rag-query") {
    ensure(config, user, "rag:ask");
    if (!key)
      fail(config, 422, "중복 요청 방지 키가 필요합니다.", "VALIDATION_ERROR");
    if (!body.question?.trim() || body.question.trim().length > 2000)
      fail(
        config,
        422,
        "질문은 1~2,000자로 입력해 주세요.",
        "VALIDATION_ERROR",
      );
    const result = resultFor(
      body.question.trim(),
      user,
      body.as_of,
      body.conversation_id || id("conv"),
    );
    events.push(eventFor(body.question.trim(), user, result));
    payload = { data: result };
  } else if (path.startsWith("/conversations/")) {
    const rows = events.filter(
      (e) =>
        e.conversation_id === path.split("/")[2] &&
        e.store_id === user.store_id &&
        e.user_role === user.role,
    );
    payload = {
      data: {
        id: path.split("/")[2],
        created_at: rows[0]?.created_at || now(),
        updated_at: rows.at(-1)?.updated_at || now(),
        messages: rows.map((e) => ({
          question_id: e.question_id,
          question_redacted: e.question_redacted,
          created_at: e.created_at,
          answer_status: e.answer_status,
          result: e.result,
          error_code: e.error_code,
        })),
      },
    };
  } else if (/^\/questions\/[^/]+\/feedback$/.test(path)) {
    const qid = path.split("/")[2];
    feedback.set(qid, body.helpful);
    payload = {
      data: { question_id: qid, helpful: body.helpful, submitted_at: now() },
    };
  } else if (path === "/documents" && method === "post") {
    ensure(config, user, "document:write");
    const metadata = JSON.parse(body.get("metadata")),
      file = body.get("file");
    if (
      versions.some(
        (v) =>
          v.document_id === metadata.document_id &&
          v.version === metadata.version,
      )
    )
      fail(config, 409, "이미 등록된 판본입니다.", "VERSION_CONFLICT");
    const docid = metadata.document_id || id("doc"),
      vid = `${docid}-${metadata.version}`;
    const v = {
      ...metadata,
      document_id: docid,
      version_id: vid,
      brand_id: user.brand_id,
      lifecycle: "draft",
      index_status: "not_started",
      created_at: now(),
      approved_at: null,
      published_at: null,
      file_name: file.name,
      media_type: file.type || "text/plain",
      file_size_bytes: file.size,
      sha256: Array.from(
        new Uint8Array(
          await crypto.subtle.digest("SHA-256", await file.arrayBuffer()),
        ),
        (byte) => byte.toString(16).padStart(2, "0"),
      ).join(""),
      page_count: null,
      file_url: `/api/v1/documents/${docid}/versions/${vid}/file`,
      index_revision: null,
    };
    const reviewed = materialFor(v);
    if (reviewed) v.page_count = reviewed.document.page_count;
    versions.push(v);
    files.set(vid, file);
    payload = { data: v };
    status = 201;
  } else if (path === "/documents") {
    if (query.management) ensure(config, user, "document:write");
    const accessible = versions.filter(
      (v) =>
        permitted(v, user) &&
        (!query.category || v.category === query.category) &&
        (query.management ||
          (v.lifecycle === "published" &&
            v.index_status === "ready" &&
            new Date(v.effective_from) <= new Date(query.as_of || now()) &&
            (!validUntil(v) ||
              new Date(query.as_of || now()) < new Date(validUntil(v))))),
    );
    const latest = [...new Set(accessible.map((v) => v.document_id))].map(
      (did) => accessible.filter((v) => v.document_id === did).at(-1),
    );
    payload = paged(
      latest.map((v) => ({
        document_id: v.document_id,
        title: v.title,
        category: v.category,
        material_type: v.material_type,
        is_demo: v.is_demo,
        visibility: v.visibility,
        selected_version_id: v.version_id,
        selected_version: v.version,
        effective_from: v.effective_from,
        lifecycle: v.lifecycle,
        index_status: v.index_status,
      })),
      query,
    );
  } else if (path.startsWith("/documents/")) {
    const [, , docid, , vid, action] = path.split("/");
    if (!vid)
      payload = paged(
        versions
          .filter((v) => v.document_id === docid && permitted(v, user))
          .reverse(),
        query,
      );
    else {
      const v = versions.find(
        (v) => v.document_id === docid && v.version_id === vid,
      );
      if (!v)
        fail(config, 404, "문서를 찾을 수 없습니다.", "RESOURCE_NOT_FOUND");
      if (!permitted(v, user))
        fail(config, 403, "이 문서를 열람할 권한이 없습니다.");
      if (action === "approve") {
        ensure(config, user, "document:approve");
        if (v.lifecycle !== "draft")
          fail(config, 409, "초안만 승인할 수 있습니다.", "INVALID_STATE");
        v.lifecycle = "approved";
        v.index_status = "queued";
        v.approved_at = now();
        const jid = id("job");
        jobs.set(jid, { version: v, started: Date.now() });
        payload = {
          data: {
            document_id: docid,
            version_id: vid,
            lifecycle: v.lifecycle,
            index_status: v.index_status,
            job_id: jid,
          },
        };
        status = 202;
      } else if (action === "publish") {
        ensure(config, user, "document:publish");
        if (v.lifecycle !== "approved" || v.index_status !== "ready")
          fail(
            config,
            409,
            "검색 준비 완료 후 배포할 수 있습니다.",
            "INDEX_NOT_READY",
          );
        for (const previous of versions.filter(
          (item) =>
            item.document_id === v.document_id &&
            item.lifecycle === "published" &&
            new Date(item.effective_from) < new Date(v.effective_from),
        )) {
          const existing = validUntil(previous);
          if (!existing || new Date(existing) > new Date(v.effective_from))
            publishedEnds.set(previous.version_id, v.effective_from);
        }
        v.lifecycle = "published";
        v.published_at = now();
        const nid = id("notice");
        notices.unshift({
          notice_id: nid,
          document_id: docid,
          version_id: vid,
          title: body.notice_title,
          change_summary: v.change_summary,
          effective_from: v.effective_from,
          published_at: now(),
          acknowledged_at: null,
          detail_url: `/api/v1/documents/${docid}/versions/${vid}`,
        });
        acknowledgements.set(nid, new Map());
        payload = {
          data: {
            document_id: docid,
            version_id: vid,
            lifecycle: v.lifecycle,
            index_status: v.index_status,
            effective_from: v.effective_from,
            published_at: v.published_at,
            notice_id: nid,
          },
        };
      } else if (action === "file") {
        if (files.has(vid)) payload = files.get(vid);
        else {
          const sections = sectionsFor(v);
          payload = new Blob(
            [
              `합성 시연 문서 발췌\n${v.title} v${v.version}\n${sections.map((item) => `${item.heading}\n${item.text}`).join("\n\n")}\n실제 원본 PDF가 아닌 시연용 TXT 발췌입니다.`,
            ],
            { type: "text/plain;charset=utf-8" },
          );
        }
      } else {
        const all = sectionsFor(v).map((item) => ({
          ...item,
          effective_until: validUntil(v),
        }));
        const paginated = paged(all, query);
        payload = {
          data: {
            version: clone(v),
            sections: paginated.data,
            sections_meta: paginated.meta,
          },
        };
      }
    }
  } else if (path.startsWith("/index-jobs/")) {
    ensure(config, user, "document:write");
    const jid = path.split("/")[2],
      job = jobs.get(jid);
    if (!job)
      fail(config, 404, "작업을 찾을 수 없습니다.", "RESOURCE_NOT_FOUND");
    const done = Date.now() - job.started > 2500;
    job.version.index_status = done ? "ready" : "running";
    if (done) job.version.index_revision = `demo-index-${job.version.version}`;
    payload = {
      data: {
        job_id: jid,
        document_id: job.version.document_id,
        version_id: job.version.version_id,
        status: done ? "succeeded" : "running",
        chunk_count: done ? 12 : 0,
        index_revision: job.version.index_revision,
        error_code: null,
        error_message: null,
        created_at: job.version.approved_at,
        updated_at: now(),
      },
    };
  } else if (path === "/notices") {
    if (user.role === "prospect")
      fail(config, 403, "운영 점주와 본사 전용입니다.");
    payload = paged(
      notices
        .map((n) => ({
          ...n,
          acknowledged_at:
            acknowledgements.get(n.notice_id)?.get(user.store_id)
              ?.acknowledged_at || null,
        }))
        .filter((n) => !query.unread || n.acknowledged_at === null),
      query,
    );
  } else if (path.startsWith("/notices/")) {
    const nid = path.split("/")[2],
      records = acknowledgements.get(nid);
    if (!records) fail(config, 404, "공지가 없습니다.", "RESOURCE_NOT_FOUND");
    if (path.endsWith("/ack")) {
      if (user.role !== "owner")
        fail(config, 403, "대상 점주만 확인할 수 있습니다.");
      if (!records.has(user.store_id))
        records.set(user.store_id, {
          notice_id: nid,
          store_id: user.store_id,
          acknowledged_by: user.id,
          acknowledged_at: now(),
        });
      payload = { data: records.get(user.store_id) };
    } else {
      ensure(config, user, "analytics:read");
      let rows = stores.map((s) => ({
        store_id: s.id,
        store_name: s.name,
        region_code: s.region_code,
        acknowledged_at: records.get(s.id)?.acknowledged_at || null,
        acknowledged_by: records.get(s.id)?.acknowledged_by || null,
      }));
      if (query.acknowledged !== undefined && query.acknowledged !== "")
        rows = rows.filter(
          (r) =>
            !!r.acknowledged_at === (String(query.acknowledged) === "true"),
        );
      if (query.region_code)
        rows = rows.filter((r) => r.region_code === query.region_code);
      payload = paged(rows, query, {
        summary: {
          notice_id: nid,
          as_of: now(),
          target_count: stores.length,
          acknowledged_count: records.size,
          unacknowledged_count: stores.length - records.size,
          acknowledgement_rate: records.size / stores.length,
        },
      });
    }
  } else if (["/regions", "/stores", "/topics"].includes(path)) {
    ensure(config, user, "analytics:read");
    let rows =
      path === "/regions" ? regions : path === "/stores" ? stores : topics;
    if (query.region_code)
      rows = rows.filter((x) => x.region_code === query.region_code);
    if (query.category)
      rows = rows.filter((x) => x.category === query.category);
    payload = paged(rows, query);
  } else if (path === "/analytics/questions") {
    ensure(config, user, "analytics:read");
    const rows = filteredEvents(query),
      metrics = counts(rows),
      start = query.week_start
        ? `${query.week_start}T00:00:00+09:00`
        : query.from || `${monday}T00:00:00+09:00`,
      end =
        query.to ||
        new Date(new Date(start).valueOf() + 7 * 86400000).toISOString();
    payload = {
      data: {
        period: {
          start,
          end_exclusive: end,
          timezone: "Asia/Seoul",
          is_partial: new Date(end) > new Date(),
          comparison_start: new Date(
            new Date(start).valueOf() - 7 * 86400000,
          ).toISOString(),
          comparison_end_exclusive: start,
        },
        filters: {
          region_code: query.region_code || null,
          store_id: query.store_id || null,
          category: query.category || null,
          user_role: query.user_role || "owner",
          topic_id: query.topic_id || null,
        },
        generated_at: now(),
        metrics,
        previous_period: counts([]),
        total_change: metrics.total,
        total_change_rate: null,
        change_kind: metrics.total ? "new" : "no_data",
        top_topics: topics
          .map((t) => ({
            topic_id: t.id,
            label: t.label,
            count: rows.filter((e) => e.topic_id === t.id).length,
            review_required_count: rows.filter(
              (e) => e.topic_id === t.id && e.answer_status !== "answered",
            ).length,
          }))
          .filter((t) => t.count)
          .sort((a, b) => b.count - a.count),
        by_category: ["recipe", "consumer", "franchise"].map((category) => ({
          category,
          count: rows.filter((e) => e.category === category).length,
        })),
        by_region: regions.map((r) => ({
          region_code: r.code,
          region_name: r.name,
          metrics: counts(rows.filter((e) => e.region_code === r.code)),
          small_sample:
            rows.filter((e) => e.region_code === r.code).length < 10,
        })),
      },
    };
  } else if (path === "/analytics/question-events") {
    ensure(config, user, "analytics:read");
    payload = paged(filteredEvents(query).slice().reverse(), query);
  } else if (path.startsWith("/analytics/question-events/")) {
    ensure(
      config,
      user,
      method === "patch" ? "analytics:write" : "analytics:read",
    );
    const event = events.find((e) => e.question_id === path.split("/").at(-1));
    if (!event)
      fail(config, 404, "문의를 찾을 수 없습니다.", "RESOURCE_NOT_FOUND");
    if (method === "patch") {
      if (event.revision !== body.expected_revision)
        fail(
          config,
          409,
          "다른 담당자가 먼저 수정했습니다. 새로고침 후 다시 시도해 주세요.",
          "INVALID_STATE",
        );
      for (const k of ["category", "topic_id", "resolution_status"])
        if (k in body) event[k] = body[k];
      event.revision++;
      event.updated_at = now();
      if ("category" in body || "topic_id" in body)
        event.classification_status = "corrected";
    }
    payload = { data: event };
  } else
    fail(
      config,
      404,
      "시연에서 지원하지 않는 요청입니다.",
      "RESOURCE_NOT_FOUND",
    );
  if (!(payload instanceof Blob))
    payload = { ...clone(payload), request_id: id("req") };
  const response = {
    data: payload,
    status,
    statusText: "OK",
    headers: new AxiosHeaders({
      "x-request-id": payload.request_id || id("req"),
    }),
    config,
  };
  if (key) cache.set(cacheKey, { fingerprint, response });
  return response;
}
function cloneResponse(response, config) {
  return { ...response, data: clone(response.data), config };
}
