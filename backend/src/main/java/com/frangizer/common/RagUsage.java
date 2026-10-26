package com.frangizer.common;

import org.springframework.web.context.request.RequestAttributes;
import org.springframework.web.context.request.RequestContextHolder;

import tools.jackson.databind.JsonNode;

/**
 * 한 요청에서 RAG가 쓴 토큰 사용량. RagService가 요청 속성에 남기고 RequestIdFilter가 요청 로그 한 줄에 붙인다.
 * 질문 원문은 담지 않는다. 토큰 값이 없으면(RAG 장애·시간 초과) null로 두고 로그에는 "-"로 남긴다.
 */
public record RagUsage(String questionId, boolean reused, Boolean llmCalled, Long inputTokens, Long outputTokens,
                       Long embeddingTokens, String model, Double costUsd) {

    public static final String ATTRIBUTE = "ragUsage";

    /** RAG 응답의 usage·retrieval 블록에서 만든다. */
    public static RagUsage of(String questionId, JsonNode usage, JsonNode retrieval, Double costUsd) {
        return new RagUsage(questionId, false,
                retrieval.path("llm_called").isBoolean() ? retrieval.path("llm_called").asBoolean() : null,
                number(usage, "input_tokens"), number(usage, "output_tokens"), number(usage, "embedding_tokens"),
                usage.path("model").isString() ? usage.path("model").asString() : null, costUsd);
    }

    /** 같은 멱등키 재전송: 저장된 결과를 돌려주므로 RAG를 다시 부르지 않는다. */
    public static RagUsage reused(String questionId) {
        return new RagUsage(questionId, true, false, 0L, 0L, 0L, null, 0.0);
    }

    /** 현재 요청에 남긴다. 요청 밖(배치·테스트 등)에서는 아무것도 하지 않는다. */
    public void record() {
        RequestAttributes attrs = RequestContextHolder.getRequestAttributes();
        if (attrs != null) attrs.setAttribute(ATTRIBUTE, this, RequestAttributes.SCOPE_REQUEST);
    }

    /** 요청 로그 뒤에 붙일 키=값 목록. */
    public String logFields() {
        return "question_id=" + text(questionId) + " reused=" + reused + " llm=" + text(llmCalled)
                + " in_tok=" + text(inputTokens) + " out_tok=" + text(outputTokens) + " emb_tok=" + text(embeddingTokens)
                + " model=" + text(model) + " cost_usd=" + (costUsd == null ? "-" : String.format("%.5f", costUsd));
    }

    private static Long number(JsonNode node, String field) {
        return node.path(field).isNumber() ? node.path(field).asLong() : null;
    }

    private static String text(Object value) {
        return value == null ? "-" : value.toString();
    }
}
