package com.frangizer.rag;

import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import com.frangizer.auth.AuthUser;
import com.frangizer.common.Api;
import com.frangizer.common.ApiException;
import com.frangizer.common.Idempotency;
import com.frangizer.rag.RagController.AskRequest;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

/** 공개 /rag-query 게이트웨이. 명세 11.1 의사코드 순서를 따른다. */
@Service
public class RagService {

    private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(RagService.class);

    private static final String OP = "rag-query";
    public static final Pattern ID = Pattern.compile("^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$");
    private static final Set<String> CATEGORIES = Set.of("franchise", "recipe", "hygiene", "consumer", "other");
    private static final Set<String> CLASSIFICATION = Set.of("pending", "classified", "failed", "corrected");

    private final JdbcClient db;
    private final Idempotency idem;
    private final RagClient rag;
    private final ObjectMapper json;
    private final TransactionTemplate tx;

    RagService(JdbcClient db, Idempotency idem, RagClient rag, ObjectMapper json, TransactionTemplate tx) {
        this.db = db;
        this.idem = idem;
        this.rag = rag;
        this.json = json;
        this.tx = tx;
    }

    public ResponseEntity<Object> ask(AuthUser user, String token, String key, AskRequest req) {
        Idempotency.validateKey(key);
        user.require("rag:ask");
        String question = req.question() == null ? "" : req.question().strip();
        if (question.isEmpty()) throw ApiException.invalid("question", "too_short", "질문을 입력해 주세요.");
        if (question.length() > 2000) throw ApiException.invalid("question", "too_long", "질문은 2,000자 이하로 입력해 주세요.");
        if (req.conversationId() != null && !ID.matcher(req.conversationId()).matches()) {
            throw ApiException.invalid("conversation_id", "invalid", "conversation_id 형식이 올바르지 않습니다.");
        }
        Instant now = Instant.now();
        Instant asOf = req.asOf() == null ? now : req.asOf().toInstant();
        String basis = basis(asOf, now);
        String hash = idem.hash(req);
        String redacted = Redactor.redact(question);

        // 1. 질문 사건 기록(재시도면 같은 question_id 재사용)
        String questionId;
        String conversationId;
        Optional<Idempotency.Entry> prior = idem.find(user.id(), OP, key);
        if (prior.isPresent()) {
            if (!prior.get().requestHash().equals(hash)) throw Idempotency.conflict();
            if (prior.get().response() != null) return idem.replay(prior.get());
            questionId = prior.get().resourceId();
            String current = db.sql("SELECT answer_status FROM question_events WHERE question_id = :q")
                    .param("q", questionId).query(String.class).single();
            if ("processing".equals(current)) {
                // 첫 요청이 아직 RAG를 기다리는 중. 두 시도가 서로의 결과를 덮어쓰지 않게 막는다.
                throw new ApiException(HttpStatus.CONFLICT, "IDEMPOTENCY_CONFLICT",
                        "같은 질문을 처리하고 있습니다. 잠시 후 같은 키로 다시 시도해 주세요.", List.of(), true);
            }
            db.sql("UPDATE question_events SET attempt_count = attempt_count + 1, answer_status = 'processing', "
                            + "error_code = NULL, updated_at = :now WHERE question_id = :q")
                    .param("now", Api.now()).param("q", questionId).update();
            conversationId = db.sql("SELECT conversation_id FROM question_events WHERE question_id = :q")
                    .param("q", questionId).query(String.class).single();
        } else {
            conversationId = req.conversationId() == null ? Api.newId("conv") : ownedConversation(user, req.conversationId());
            questionId = Api.newId("q");
            String qid = questionId;
            String cid = conversationId;
            tx.executeWithoutResult(s -> {
                idem.begin(user.id(), OP, key, hash, qid);
                insertEvent(user, qid, cid, redacted);
            });
        }

        try {
            return answer(user, token, key, questionId, conversationId, redacted, asOf, basis);
        } catch (ApiException e) {
            markFailed(questionId, e.code);
            e.questionId = questionId;
            throw e;
        } catch (RuntimeException e) {
            log.error("rag-query failed question_id={}", questionId, e);
            markFailed(questionId, "INTERNAL_ERROR");
            ApiException wrapped = new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR",
                    "일시적인 오류가 발생했습니다. 요청 ID를 담당자에게 알려 주세요.");
            wrapped.questionId = questionId;
            throw wrapped;
        }
    }

    private void markFailed(String questionId, String code) {
        db.sql("UPDATE question_events SET answer_status = 'system_error', error_code = :c, updated_at = :now "
                        + "WHERE question_id = :q")
                .param("c", code).param("now", Api.now()).param("q", questionId).update();
    }

    private ResponseEntity<Object> answer(AuthUser user, String token, String key, String questionId,
                                          String conversationId, String redacted, Instant asOf, String basis) {
        // 2. 허용 판본
        List<Map<String, Object>> allowed = allowedVersions(user, asOf);
        Set<String> allowedIds = new HashSet<>();
        allowed.forEach(v -> allowedIds.add((String) v.get("version_id")));

        // 3. 내부 RAG 호출
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("question_id", questionId);
        body.put("attempt_id", questionId + "-a" + attemptCount(questionId));
        body.put("conversation_id", conversationId);
        body.put("question", redacted);
        Map<String, Object> principal = new LinkedHashMap<>();
        principal.put("user_id", user.id());
        principal.put("role", user.role());
        principal.put("brand_id", user.brandId());
        principal.put("store_id", user.storeId());
        principal.put("permissions", user.permissions());
        body.put("principal", principal);
        body.put("as_of", DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(Api.kst(asOf)));
        body.put("basis", basis);
        body.put("scope_revision", "scope-" + Idempotency.sha256(idem.hash(allowed).getBytes()).substring(0, 16));
        body.put("allowed_versions", allowed);
        body.put("history", history(conversationId, questionId));
        body.put("deadline_ms", RagClient.DEADLINE_MS);

        long started = System.nanoTime();
        JsonNode data = rag.query(token, body);
        ObjectNode result = checkResult(data.path("result"), allowedIds);
        int latency = (int) ((System.nanoTime() - started) / 1_000_000);
        result.put("question_id", questionId);
        result.put("conversation_id", conversationId);
        result.put("latency_ms", latency);
        fillSourceMetadata(result);

        // 4. 결과 기록. 분류 값이 이상해도 답변은 실패시키지 않는다.
        JsonNode cls = data.path("classification");
        String category = cls.path("category").asString(result.path("category").asString("other"));
        String clsStatus = cls.path("status").asString(result.path("classification_status").asString("pending"));
        String topic = cls.path("topic_id").isString() ? cls.path("topic_id").asString() : null;
        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("scope_revision", data.path("scope_revision"));
        meta.put("usage", data.path("usage"));
        meta.put("retrieval", data.path("retrieval"));
        Map<String, Object> response = Api.ok(result);
        tx.executeWithoutResult(s -> {
            db.sql("""
                    UPDATE question_events SET answer_status = :st, result = :r, category = :cat, topic_id = :topic,
                      classification_status = :cs, classification_version = :cv, latency_ms = :lat, rag_meta = :meta,
                      updated_at = :now
                    WHERE question_id = :q""")
                    .param("st", result.path("status").asString())
                    .param("r", json.writeValueAsString(result))
                    .param("cat", CATEGORIES.contains(category) ? category : "other")
                    .param("topic", topic)
                    .param("cs", CLASSIFICATION.contains(clsStatus) ? clsStatus : "pending")
                    .param("cv", cls.path("version").asString("none"))
                    .param("lat", latency)
                    .param("meta", json.writeValueAsString(meta))
                    .param("now", Api.now())
                    .param("q", questionId)
                    .update();
            db.sql("DELETE FROM question_sources WHERE question_id = :q").param("q", questionId).update();
            Set<String> seen = new HashSet<>();
            for (JsonNode src : result.path("sources")) {
                if (!seen.add(src.path("chunk_id").asString())) continue;
                String loc = src.path("page").isNumber() ? "p" + src.path("page").asInt() : src.path("article").asString(null);
                db.sql("INSERT INTO question_sources (question_id, chunk_id, document_id, version_id, page_or_article) "
                                + "VALUES (:q, :c, :d, :v, :l)")
                        .param("q", questionId).param("c", src.path("chunk_id").asString())
                        .param("d", src.path("document_id").asString()).param("v", src.path("version_id").asString())
                        .param("l", loc).update();
            }
            idem.complete(user.id(), OP, key, 200, response);
        });
        return ResponseEntity.ok(response);
    }

    /** RAG 응답이 계약을 어기면(허용 밖 출처 포함) 업스트림 오류로 처리한다. 권한 밖 자료를 내보내지 않기 위함. */
    private static ObjectNode checkResult(JsonNode result, Set<String> allowedIds) {
        if (!(result instanceof ObjectNode node)) throw badUpstream();
        String status = node.path("status").asString("");
        if (!Set.of("answered", "needs_review", "handoff").contains(status)) throw badUpstream();
        // 상태 조합 고정: answered → false/false, needs_review → true/false, handoff → true/true
        if (node.path("needs_review").asBoolean() != !"answered".equals(status)
                || node.path("handoff").asBoolean() != "handoff".equals(status)) throw badUpstream();
        boolean hasReason = node.path("handoff_reason").isString() && !node.path("handoff_reason").asString().isBlank();
        if ("handoff".equals(status) && !hasReason) throw badUpstream();
        if ("needs_review".equals(status) && !hasReason && node.path("follow_up_questions").isEmpty()) throw badUpstream();
        for (JsonNode src : node.path("sources")) {
            if (!allowedIds.contains(src.path("version_id").asString())
                    || !src.path("chunk_id").isString() || src.path("excerpt").asString("").length() > 1500) {
                throw badUpstream();
            }
        }
        if ("answered".equals(status) && node.path("sources").isEmpty()) throw badUpstream();
        return node;
    }

    /**
     * 출처의 문서 메타데이터(제목·판본·자료 성격·합성 여부·링크)는 DB 값으로 덮어쓴다.
     * "합성·시연 자료" 표시가 RAG 응답 품질에 좌우되지 않게 하기 위함. 조문 단위 적용일은 RAG 값이 있으면 유지한다.
     */
    private void fillSourceMetadata(ObjectNode result) {
        for (JsonNode node : result.path("sources")) {
            ObjectNode src = (ObjectNode) node;
            String docId = src.path("document_id").asString();
            String verId = src.path("version_id").asString();
            db.sql("SELECT title, version, material_type, is_demo, effective_from, effective_until "
                            + "FROM document_versions WHERE version_id = :v AND document_id = :d")
                    .param("v", verId).param("d", docId)
                    .query((rs, n) -> {
                        src.put("title", rs.getString("title"));
                        src.put("version", rs.getString("version"));
                        src.put("material_type", rs.getString("material_type"));
                        src.put("is_demo", rs.getBoolean("is_demo"));
                        if (!src.path("effective_from").isString()) {
                            src.put("effective_from", DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(Api.kst(rs, "effective_from")));
                        }
                        if (!src.has("effective_until")) {
                            var until = Api.kst(rs, "effective_until");
                            if (until == null) src.putNull("effective_until");
                            else src.put("effective_until", DateTimeFormatter.ISO_OFFSET_DATE_TIME.format(until));
                        }
                        return 0;
                    })
                    .optional()
                    .orElseThrow(RagService::badUpstream); // 허용 판본 검사를 통과했으므로 정상이면 항상 존재
            String base = "/api/v1/documents/" + docId + "/versions/" + verId;
            src.put("detail_url", base);
            src.put("file_url", base + "/file");
            if (!src.has("location")) {
                src.put("location", src.path("page").isNumber() ? "PDF " + src.path("page").asInt() + "쪽" : src.path("article").asString(""));
            }
            if (!src.has("excerpt")) src.put("excerpt", "");
            if (!src.has("article")) src.putNull("article");
        }
    }

    private static ApiException badUpstream() {
        return new ApiException(HttpStatus.BAD_GATEWAY, "UPSTREAM_ERROR", "답변 생성 서비스에서 오류가 발생했습니다. 다시 시도해 주세요.");
    }

    private String ownedConversation(AuthUser user, String conversationId) {
        String owner = db.sql("SELECT user_id FROM question_events WHERE conversation_id = :c LIMIT 1")
                .param("c", conversationId).query(String.class).optional()
                .orElseThrow(ApiException::notFound);
        if (!owner.equals(user.id())) throw ApiException.forbidden();
        return conversationId;
    }

    private void insertEvent(AuthUser user, String questionId, String conversationId, String redacted) {
        Map<String, Object> store = user.storeId() == null ? Map.of() : db.sql("""
                        SELECT s.name AS store_name, s.region_code, r.name AS region_name
                        FROM stores s LEFT JOIN regions r ON r.code = s.region_code WHERE s.store_id = :s""")
                .param("s", user.storeId()).query().singleRow();
        Map<String, Object> p = new java.util.HashMap<>();
        p.put("q", questionId);
        p.put("req", Api.requestId());
        p.put("c", conversationId);
        p.put("b", user.brandId());
        p.put("u", user.id());
        p.put("role", user.role());
        p.put("s", user.storeId());
        p.put("sn", store.get("store_name"));
        p.put("rc", store.get("region_code"));
        p.put("rn", store.get("region_name"));
        p.put("now", Api.now());
        p.put("text", redacted);
        db.sql("""
                INSERT INTO question_events (question_id, request_id, conversation_id, brand_id, user_id, user_role,
                  store_id, store_name, region_code, region_name, created_at, updated_at, question_redacted, answer_status)
                VALUES (:q, :req, :c, :b, :u, :role, :s, :sn, :rc, :rn, :now, :now, :text, 'processing')""")
                .params(p).update();
    }

    /** published ∧ ready ∧ 같은 브랜드 ∧ visibility 허용 ∧ effective_from ≤ as_of < effective_until. */
    private List<Map<String, Object>> allowedVersions(AuthUser user, Instant asOf) {
        return db.sql("""
                        SELECT v.document_id, v.version_id, v.index_revision, v.visibility
                        FROM document_versions v JOIN documents d ON d.document_id = v.document_id
                        WHERE d.brand_id = :b AND v.lifecycle = 'published' AND v.index_status = 'ready'
                          AND v.index_revision IS NOT NULL
                          AND v.effective_from <= :t AND (v.effective_until IS NULL OR v.effective_until > :t)
                        ORDER BY v.version_id""")
                .param("b", user.brandId()).param("t", Api.ts(asOf))
                .query((rs, n) -> user.canSee(rs.getString("visibility")) ? Map.<String, Object>of(
                        "document_id", rs.getString("document_id"),
                        "version_id", rs.getString("version_id"),
                        "index_revision", rs.getString("index_revision")) : null)
                .list().stream().filter(java.util.Objects::nonNull)
                .toList();
    }

    /** ponytail: KST 날짜 기준으로 과거/현재/미래를 나눈다. 시각 단위 판정이 필요하면 판본 집합 비교로 바꾼다. */
    /** as_of 생략 시 asOf == now → current. 명시한 시각은 날짜가 아니라 시각까지 현재와 비교한다. */
    static String basis(Instant asOf, Instant now) {
        int c = asOf.compareTo(now);
        return c < 0 ? "past" : c > 0 ? "future" : "current";
    }

    private int attemptCount(String questionId) {
        return db.sql("SELECT attempt_count FROM question_events WHERE question_id = :q")
                .param("q", questionId).query(Integer.class).single();
    }

    /** 같은 대화의 직전 답변 최대 6턴(오래된 순). */
    private List<Map<String, String>> history(String conversationId, String questionId) {
        List<Map<String, String>> turns = new ArrayList<>(db.sql("""
                        SELECT question_redacted, result FROM question_events
                        WHERE conversation_id = :c AND question_id <> :q AND result IS NOT NULL
                        ORDER BY created_at DESC LIMIT 6""")
                .param("c", conversationId).param("q", questionId)
                .query((rs, n) -> Map.of(
                        "question", cut(rs.getString(1), 2000),
                        "answer", cut(json.readTree(rs.getString(2)).path("answer").asString(""), 6000)))
                .list());
        Collections.reverse(turns);
        return turns;
    }

    private static String cut(String s, int max) {
        return s.length() <= max ? s : s.substring(0, max);
    }
}
