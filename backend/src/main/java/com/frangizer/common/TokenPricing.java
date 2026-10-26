package com.frangizer.common;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 100만 토큰당 단가(USD)로 요청당 추정 비용을 계산한다. 단가는 환경변수로 받으며 모델을 바꾸면 함께 바꾼다.
 * 입력·출력 단가가 모두 0이면 단가 미설정으로 보고 null을 돌려준다(0달러로 오해하지 않게).
 * 캐시 적중 여부는 RAG가 알려주지 않으므로 모든 입력을 기본 입력 단가로 계산한다(상한 추정).
 */
@Component
public class TokenPricing {

    private static final double PER_TOKEN = 1_000_000.0;

    private final double inputPer1m;
    private final double outputPer1m;
    private final double embeddingPer1m;

    public TokenPricing(@Value("${app.pricing.input-per-1m:0}") double inputPer1m,
                 @Value("${app.pricing.output-per-1m:0}") double outputPer1m,
                 @Value("${app.pricing.embedding-per-1m:0}") double embeddingPer1m) {
        this.inputPer1m = inputPer1m;
        this.outputPer1m = outputPer1m;
        this.embeddingPer1m = embeddingPer1m;
    }

    public Double estimate(Long inputTokens, Long outputTokens, Long embeddingTokens) {
        if (inputPer1m <= 0 && outputPer1m <= 0) return null;
        if (inputTokens == null && outputTokens == null) return null;
        double cost = value(inputTokens) * inputPer1m + value(outputTokens) * outputPer1m
                + value(embeddingTokens) * embeddingPer1m;
        return Math.round(cost / PER_TOKEN * 1e8) / 1e8;
    }

    private static double value(Long tokens) {
        return tokens == null ? 0 : tokens;
    }
}
