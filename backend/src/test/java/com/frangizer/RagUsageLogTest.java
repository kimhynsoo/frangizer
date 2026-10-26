package com.frangizer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

import java.nio.charset.StandardCharsets;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import com.frangizer.common.TokenPricing;
import com.frangizer.rag.RagClient;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** 요청 로그 한 줄에 RAG 토큰 사용량·추정 비용이 붙고, 질문 원문은 남지 않는지 확인한다. */
@SpringBootTest(properties = {"app.pricing.input-per-1m=0.75", "app.pricing.output-per-1m=4.50"})
@org.springframework.test.context.ActiveProfiles("test")
@AutoConfigureMockMvc
@ExtendWith(OutputCaptureExtension.class)
class RagUsageLogTest {

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;
    @Autowired JdbcClient db;
    @MockitoBean RagClient rag;

    @Test
    void requestLogCarriesTokensAndCost(CapturedOutput output) throws Exception {
        when(rag.query(anyString(), any())).thenReturn(json.readTree("""
                {"result":{"question_id":"x","conversation_id":"x","answer":"근거를 확인하지 못했습니다.",
                  "steps":[],"status":"needs_review","needs_review":true,"handoff":false,"sources":[],
                  "follow_up_questions":["어느 메뉴인가요?"],"handoff_reason":null,"category":"other",
                  "as_of":"2026-10-26T10:00:00+09:00","basis":"current","latency_ms":1},
                 "scope_revision":"s","classification":{"category":"other","status":"pending","version":"none"},
                 "usage":{"input_tokens":75191,"output_tokens":929,"embedding_tokens":20,"model":"gpt-5.4-mini",
                   "embedding_model":"text-embedding-3-small","estimated_cost_usd":null},
                 "retrieval":{"candidate_count":3,"used_chunk_ids":[],"llm_called":true}}"""));
        String token = login();
        String question = "{\"question\":\"토큰로그확인용 질문입니다\"}";

        JsonNode first = ask(token, "key-usage-0001", question);
        String qid = first.at("/data/question_id").asString();

        assertThat(output.getOut()).contains("path=/api/v1/rag-query status=200")
                .contains("question_id=" + qid + " reused=false llm=true in_tok=75191 out_tok=929 emb_tok=20 "
                        + "model=gpt-5.4-mini cost_usd=0.06057")
                .doesNotContain("토큰로그확인용");

        // 추정 비용은 질문 기록(rag_meta.usage)에도 남는다
        String meta = db.sql("SELECT rag_meta FROM question_events WHERE question_id = :q").param("q", qid)
                .query(String.class).single();
        assertThat(json.readTree(meta).at("/usage/estimated_cost_usd").asDouble()).isEqualTo(0.06057375);

        // 같은 키 재전송은 RAG를 다시 부르지 않으므로 토큰 0으로 남긴다
        ask(token, "key-usage-0001", question);
        assertThat(output.getOut()).contains("question_id=" + qid + " reused=true llm=false in_tok=0 out_tok=0");
    }

    @Test
    void pricingIsOptional() {
        TokenPricing off = new TokenPricing(0, 0, 0);
        assertThat(off.estimate(1000L, 100L, 10L)).isNull();
        TokenPricing on = new TokenPricing(0.75, 4.50, 0.02);
        assertThat(on.estimate(1_000_000L, 0L, 0L)).isEqualTo(0.75);
        assertThat(on.estimate(null, null, null)).isNull();
    }

    private String login() throws Exception {
        String body = mvc.perform(post("/api/v1/login").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"demo-owner-a003\",\"password\":\"demo-password\"}"))
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        return json.readTree(body).path("accessToken").asString();
    }

    private JsonNode ask(String token, String key, String content) throws Exception {
        String body = mvc.perform(post("/api/v1/rag-query").header("Authorization", "Bearer " + token)
                        .header("Idempotency-Key", key).contentType(MediaType.APPLICATION_JSON).content(content))
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        return json.readTree(body);
    }
}
