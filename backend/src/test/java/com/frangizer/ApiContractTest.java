package com.frangizer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;

import java.nio.charset.StandardCharsets;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.temporal.TemporalAdjusters;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.AbstractMockHttpServletRequestBuilder;

import com.frangizer.common.Api;
import com.frangizer.rag.RagClient;
import com.frangizer.rag.Redactor;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** 명세 11.4 통합 체크리스트의 백엔드 부분. RAG는 목으로 대체한다. */
@SpringBootTest
@org.springframework.test.context.ActiveProfiles("test")
@AutoConfigureMockMvc
class ApiContractTest {

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;
    @MockitoBean RagClient rag;

    @Test
    void redactsContactsAndOrderNumbers() {
        String out = Redactor.redact("고객 홍길동 님이 010-1234-5678로 연락, 주문번호 A1234567 환불 요청");
        assertThat(out).doesNotContain("홍길동", "010-1234-5678", "A1234567")
                .contains("[이름]", "[전화번호]", "[주문번호]");
        assertThat(Redactor.redact("바닐라 시럽 20 mL 몇 펌프예요?")).isEqualTo("바닐라 시럽 20 mL 몇 펌프예요?");
    }

    @Test
    void authAndValidation() throws Exception {
        assertThat(call(get("/api/v1/me"), null).getResponse().getStatus()).isEqualTo(401);
        assertThat(body(call(post("/api/v1/login").contentType(MediaType.APPLICATION_JSON)
                .content("{\"username\":\"demo-owner-a001\",\"password\":\"wrong\"}"), null)).at("/error/code").asString())
                .isEqualTo("AUTH_FAILED");

        String owner = login("demo-owner-a001");
        JsonNode me = body(call(get("/api/v1/me"), owner));
        assertThat(me.at("/data/store_id").asString()).isEqualTo("demo-store-01");
        assertThat(me.at("/data/region_code").asString()).isEqualTo("SEOUL");

        // 범위 값을 본문으로 보내면 422
        MvcResult r = call(post("/api/v1/rag-query").header("Idempotency-Key", "key-scope-0001")
                .contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"질문\",\"role\":\"hq\"}"), owner);
        assertThat(r.getResponse().getStatus()).isEqualTo(422);
        assertThat(body(r).at("/error/details/0/field").asString()).isEqualTo("role");
        // owner는 본사 집계 불가
        assertThat(call(get("/api/v1/analytics/questions"), owner).getResponse().getStatus()).isEqualTo(403);
    }

    @Test
    void documentLifecycleNoticesAndQuestions() throws Exception {
        String hq = login("demo-hq-01");
        String owner = login("demo-owner-a007");
        String prospect = login("demo-prospect-01");

        // 등록 → draft
        String meta = """
                {"document_id":"test-recipes","title":"합성 카페 표준 레시피","version":"1.1","category":"recipe",
                 "material_type":"synthetic","is_demo":true,"visibility":"owner",
                 "effective_from":"2026-01-01T00:00:00+09:00","change_summary":"시럽 20→15 mL",
                 "original_source":{"title":"합성 레시피","url":null,"edition":"1.1","publisher":"프로젝트 팀",
                   "acquired_on":"2026-10-23","note":"합성 데이터"},
                 "indexing_policy":{"exclude_pages":[1],"legal_temporal_reviewed":false}}""";
        MvcResult up = call(multipart("/api/v1/documents")
                .file(new MockMultipartFile("file", "recipe_v1.1.md", "text/markdown", "# 바닐라라테\n시럽 15 mL".getBytes(StandardCharsets.UTF_8)))
                .file(new MockMultipartFile("metadata", "", "application/json", meta.getBytes(StandardCharsets.UTF_8)))
                .header("Idempotency-Key", "key-upload-0001"), hq);
        assertThat(up.getResponse().getStatus()).isEqualTo(201);
        assertThat(up.getResponse().getHeader("Location")).isEqualTo("/api/v1/documents/test-recipes/versions/test-recipes-1.1");
        assertThat(body(up).at("/data/lifecycle").asString()).isEqualTo("draft");
        String base = "/api/v1/documents/test-recipes/versions/test-recipes-1.1";
        // 미배포 판본은 점주가 볼 수 없다
        assertThat(call(get(base), owner).getResponse().getStatus()).isEqualTo(403);

        // 승인 → 색인 작업(진행 중) → 배포 시도 409 INDEX_NOT_READY
        when(rag.submitIndexJob(any())).thenReturn(json.readTree("{\"status\":\"running\",\"chunk_count\":0}"));
        JsonNode approval = body(call(post(base + "/approve").header("Idempotency-Key", "key-approve-001")
                .contentType(MediaType.APPLICATION_JSON).content("{\"review_note\":\"검수 완료\"}"), hq));
        assertThat(approval.at("/data/index_status").asString()).isEqualTo("running");
        String jobId = approval.at("/data/job_id").asString();
        assertThat(body(call(post(base + "/publish").header("Idempotency-Key", "key-publish-01")
                .contentType(MediaType.APPLICATION_JSON).content("{\"notice_title\":\"v1.1 안내\"}"), hq))
                .at("/error/code").asString()).isEqualTo("INDEX_NOT_READY");

        // 색인 완료 → 배포 → 공지
        when(rag.submitIndexJob(any())).thenReturn(json.readTree(
                "{\"status\":\"succeeded\",\"chunk_count\":7,\"index_revision\":\"rev-1\"}"));
        assertThat(body(call(get("/api/v1/index-jobs/" + jobId), hq)).at("/data/status").asString()).isEqualTo("succeeded");
        JsonNode published = body(call(post(base + "/publish").header("Idempotency-Key", "key-publish-02")
                .contentType(MediaType.APPLICATION_JSON).content("{\"notice_title\":\"v1.1 안내\"}"), hq));
        String noticeId = published.at("/data/notice_id").asString();
        assertThat(noticeId).isNotBlank();

        // 공지 확인: 미확인 1건 → ack → 재ack 같은 기록, prospect는 403
        assertThat(body(call(get("/api/v1/notices?unread=true"), owner)).at("/meta/total").asInt()).isEqualTo(1);
        JsonNode ack1 = body(call(put("/api/v1/notices/" + noticeId + "/ack").contentType(MediaType.APPLICATION_JSON).content("{}"), owner));
        JsonNode ack2 = body(call(put("/api/v1/notices/" + noticeId + "/ack"), owner));
        assertThat(ack2.at("/data/acknowledged_at")).isEqualTo(ack1.at("/data/acknowledged_at"));
        assertThat(call(put("/api/v1/notices/" + noticeId + "/ack"), prospect).getResponse().getStatus()).isEqualTo(403);
        JsonNode acks = body(call(get("/api/v1/notices/" + noticeId + "/acknowledgements?acknowledged=false"), hq));
        assertThat(acks.at("/summary/target_count").asInt()).isEqualTo(12);
        assertThat(acks.at("/summary/acknowledged_count").asInt()).isEqualTo(1);
        assertThat(acks.at("/meta/total").asInt()).isEqualTo(11);

        // 질문: 허용 판본만 RAG로 전달, 같은 키 재전송은 같은 question_id
        when(rag.query(anyString(), any())).thenReturn(json.readTree("""
                {"result":{"question_id":"x","conversation_id":"x","answer":"합성 시연 레시피 기준 15 mL입니다.",
                  "steps":[],"status":"answered","needs_review":false,"handoff":false,
                  "sources":[{"chunk_id":"chk-1","document_id":"test-recipes","version_id":"test-recipes-1.1",
                    "page":2,"article":null}],
                  "follow_up_questions":[],"handoff_reason":null,"category":"recipe","topic_id":"vanilla_syrup",
                  "classification_status":"classified","as_of":"2026-10-23T10:00:00+09:00","basis":"current","latency_ms":1},
                 "scope_revision":"s","classification":{"category":"recipe","topic_id":"vanilla_syrup",
                   "status":"classified","version":"rules-v1"},
                 "usage":{},"retrieval":{"candidate_count":1,"used_chunk_ids":["chk-1"],"llm_called":true}}"""));
        String ask = "{\"question\":\"바닐라라테 시럽 몇 mL예요? 고객 010-1111-2222\"}";
        JsonNode a1 = body(call(post("/api/v1/rag-query").header("Idempotency-Key", "key-ask-00001")
                .contentType(MediaType.APPLICATION_JSON).content(ask), owner));
        JsonNode a2 = body(call(post("/api/v1/rag-query").header("Idempotency-Key", "key-ask-00001")
                .contentType(MediaType.APPLICATION_JSON).content(ask), owner));
        String qid = a1.at("/data/question_id").asString();
        assertThat(qid).startsWith("q-").isEqualTo(a2.at("/data/question_id").asString());
        // 출처 메타데이터는 DB 값으로 채운다(합성 표시 보장)
        assertThat(a1.at("/data/sources/0/material_type").asString()).isEqualTo("synthetic");
        assertThat(a1.at("/data/sources/0/is_demo").asBoolean()).isTrue();
        assertThat(a1.at("/data/sources/0/title").asString()).isEqualTo("합성 카페 표준 레시피");
        assertThat(a1.at("/data/sources/0/file_url").asString()).isEqualTo("/api/v1/documents/test-recipes/versions/test-recipes-1.1/file");
        assertThat(body(call(post("/api/v1/rag-query").header("Idempotency-Key", "key-ask-00001")
                .contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"다른 질문\"}"), owner))
                .at("/error/code").asString()).isEqualTo("IDEMPOTENCY_CONFLICT");

        // 대화 기록은 마스킹본, 다른 사용자는 403
        String conv = a1.at("/data/conversation_id").asString();
        JsonNode history = body(call(get("/api/v1/conversations/" + conv), owner));
        assertThat(history.at("/data/messages/0/question_redacted").asString()).contains("[전화번호]").doesNotContain("1111");
        assertThat(call(get("/api/v1/conversations/" + conv), prospect).getResponse().getStatus()).isEqualTo(403);
        assertThat(body(call(put("/api/v1/questions/" + qid + "/feedback").contentType(MediaType.APPLICATION_JSON)
                .content("{\"helpful\":true}"), owner)).at("/data/helpful").asBoolean()).isTrue();

        // RAG 장애는 근거 부족으로 숨기지 않고 503 + question_id
        when(rag.query(anyString(), any())).thenThrow(new com.frangizer.common.ApiException(
                org.springframework.http.HttpStatus.SERVICE_UNAVAILABLE, "SERVICE_UNAVAILABLE", "down"));
        MvcResult failed = call(post("/api/v1/rag-query").header("Idempotency-Key", "key-ask-00002")
                .contentType(MediaType.APPLICATION_JSON).content("{\"question\":\"위생교육 언제?\"}"), owner);
        assertThat(failed.getResponse().getStatus()).isEqualTo(503);
        assertThat(body(failed).at("/error/question_id").asString()).startsWith("q-");

        // 이번 주 집계: 중복 없이 2건(answered 1, system_error 1)
        String monday = LocalDate.now(Api.KST).with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY)).toString();
        JsonNode stats = body(call(get("/api/v1/analytics/questions?week_start=" + monday), hq));
        assertThat(stats.at("/data/period/is_partial").asBoolean()).as(stats.toString()).isTrue();
        assertThat(stats.at("/data/metrics/total").asInt()).isEqualTo(2);
        assertThat(stats.at("/data/metrics/answered_count").asInt()).isEqualTo(1);
        assertThat(stats.at("/data/metrics/guidance_rate").asDouble()).isEqualTo(1.0);
        assertThat(stats.at("/data/by_region/0/region_code").asString()).isEqualTo("CHUNGCHEONG");
        assertThat(stats.at("/data/top_topics/0/topic_id").asString()).isEqualTo("vanilla_syrup");
        assertThat(call(get("/api/v1/analytics/questions?week_start=2026-10-13"), hq).getResponse().getStatus()).isEqualTo(422);

        // 처리 상태 수정: 낙관적 잠금
        JsonNode patched = body(call(patch("/api/v1/analytics/question-events/" + qid).contentType(MediaType.APPLICATION_JSON)
                .content("{\"expected_revision\":1,\"resolution_status\":\"in_progress\"}"), hq));
        assertThat(patched.at("/data/revision").asInt()).isEqualTo(2);
        assertThat(body(call(patch("/api/v1/analytics/question-events/" + qid).contentType(MediaType.APPLICATION_JSON)
                .content("{\"expected_revision\":1,\"resolution_status\":\"closed\"}"), hq)).at("/error/code").asString())
                .isEqualTo("INVALID_STATE");
    }

    private String login(String username) throws Exception {
        return body(call(post("/api/v1/login").contentType(MediaType.APPLICATION_JSON)
                .content("{\"username\":\"" + username + "\",\"password\":\"demo-password\"}"), null))
                .path("accessToken").asString();
    }

    private MvcResult call(AbstractMockHttpServletRequestBuilder<?> req, String token) throws Exception {
        if (token != null) req.header("Authorization", "Bearer " + token);
        return mvc.perform(req).andReturn();
    }

    private JsonNode body(MvcResult r) throws Exception {
        assertThat(r.getResponse().getHeader("X-Request-ID")).startsWith("req-");
        return json.readTree(r.getResponse().getContentAsString(StandardCharsets.UTF_8));
    }
}
