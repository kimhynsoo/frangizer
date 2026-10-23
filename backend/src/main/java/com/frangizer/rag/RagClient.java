package com.frangizer.rag;

import java.net.http.HttpClient;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.function.Supplier;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.BufferingClientHttpRequestFactory;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;

import com.frangizer.common.ApiException;

import tools.jackson.databind.JsonNode;

/** 내부 FastAPI RAG(:8001) 호출. X-Internal-Token은 서버 간 비밀이며 브라우저로 나가지 않는다. */
@Component
public class RagClient {

    private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(RagClient.class);

    public static final int DEADLINE_MS = 20_000;

    private final RestClient http;

    RagClient(@Value("${app.rag-url}") String baseUrl,
              @Value("${app.internal-token}") String internalToken) {
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(
                HttpClient.newBuilder()
                        .version(HttpClient.Version.HTTP_1_1) // uvicorn은 h2c 업그레이드를 받지 않는다
                        .connectTimeout(Duration.ofSeconds(3)).build());
        factory.setReadTimeout(Duration.ofMillis(DEADLINE_MS + 5_000));
        // 본문을 버퍼링해 Content-Length로 보낸다(chunked 미지원 서버 대비)
        this.http = RestClient.builder().baseUrl(baseUrl).requestFactory(new BufferingClientHttpRequestFactory(factory))
                .defaultHeader("X-Internal-Token", internalToken)
                .build();
    }

    /** POST /rag-query → InternalAnswer(data). 사용자 JWT를 그대로 넘겨 RAG가 재검증한다. */
    public JsonNode query(String userJwt, Object body) {
        return call(() -> http.post().uri("/rag-query")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + userJwt)
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve().body(JsonNode.class));
    }

    /** POST /internal/v1/index-jobs → IndexJob(data). job_id가 멱등키라 같은 내용으로 재호출해도 안전하다. */
    public JsonNode submitIndexJob(Object body) {
        return call(() -> http.post().uri("/internal/v1/index-jobs")
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve().body(JsonNode.class));
    }

    private static JsonNode call(Supplier<JsonNode> request) {
        try {
            JsonNode body = request.get();
            if (body == null || !body.path("data").isObject()) throw upstream();
            return body.path("data");
        } catch (RestClientResponseException e) {
            log.warn("rag call returned {}", e.getStatusCode());
            int status = e.getStatusCode().value();
            if (status == 504) throw timeout();
            if (status == 503) throw unavailable();
            throw upstream();
        } catch (ResourceAccessException e) {
            log.warn("rag call failed: {}", e.getMessage());
            if (e.getCause() instanceof HttpTimeoutException) throw timeout();
            throw unavailable();
        } catch (RestClientException e) {
            log.warn("rag response unreadable: {}", e.getMessage()); // JSON이 아닌 200 등
            throw upstream();
        }
    }

    private static ApiException upstream() {
        return new ApiException(HttpStatus.BAD_GATEWAY, "UPSTREAM_ERROR", "답변 생성 서비스에서 오류가 발생했습니다. 다시 시도해 주세요.");
    }

    private static ApiException timeout() {
        return new ApiException(HttpStatus.GATEWAY_TIMEOUT, "UPSTREAM_TIMEOUT", "답변 생성이 지연되고 있습니다. 다시 시도해 주세요.");
    }

    private static ApiException unavailable() {
        return new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "SERVICE_UNAVAILABLE", "답변 서비스가 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.");
    }
}
