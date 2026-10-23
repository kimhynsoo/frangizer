package com.frangizer.stt;

import java.net.http.HttpClient;
import java.time.Duration;
import java.util.Map;
import java.util.Set;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.multipart.MultipartFile;

import com.frangizer.auth.AuthUser;
import com.frangizer.common.Api;
import com.frangizer.common.ApiException;

import tools.jackson.databind.JsonNode;

/**
 * POST /stt(선택 기능): 음성을 내부 음성 서비스(:8008)로 넘겨 텍스트만 돌려준다.
 * 자동 질의하지 않고, 질문 통계에 넣지 않으며, 음성 원본은 저장하지 않는다.
 */
@RestController
public class SttController {

    private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(SttController.class);
    private static final Set<String> TYPES = Set.of("audio/webm", "audio/wav", "audio/x-wav", "audio/wave");

    private final RestClient http;

    SttController(@Value("${app.voice-url}") String voiceUrl, @Value("${app.internal-token}") String internalToken) {
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(3)).build());
        factory.setReadTimeout(Duration.ofSeconds(60)); // CPU Whisper는 60초 음성에 수십 초가 걸릴 수 있다
        this.http = RestClient.builder().baseUrl(voiceUrl).requestFactory(factory)
                .defaultHeader("X-Internal-Token", internalToken).build();
    }

    @PostMapping("/api/v1/stt")
    Map<String, Object> transcribe(@RequestAttribute(AuthUser.ATTR) AuthUser user, @RequestPart("audio") MultipartFile audio) {
        user.require("rag:ask");
        String type = audio.getContentType() == null ? "" : audio.getContentType().split(";")[0].strip().toLowerCase();
        if (!TYPES.contains(type)) {
            throw new ApiException(HttpStatus.UNSUPPORTED_MEDIA_TYPE, "UNSUPPORTED_MEDIA_TYPE", "WebM 또는 WAV 음성만 보낼 수 있습니다.");
        }
        if (audio.isEmpty()) throw ApiException.invalid("audio", "empty", "녹음된 음성이 없습니다.");

        LinkedMultiValueMap<String, Object> form = new LinkedMultiValueMap<>();
        try {
            byte[] bytes = audio.getBytes();
            form.add("audio", new ByteArrayResource(bytes) {
                @Override
                public String getFilename() {
                    return "audio"; // 사용자 파일명은 넘기지 않는다
                }
            });
        } catch (java.io.IOException e) {
            throw new IllegalStateException("audio read failed", e);
        }

        JsonNode data;
        try {
            JsonNode body = http.post().uri("/internal/v1/stt").contentType(MediaType.MULTIPART_FORM_DATA)
                    .body(form).retrieve().body(JsonNode.class);
            data = body == null ? null : body.path("data");
        } catch (RestClientResponseException e) {
            int status = e.getStatusCode().value();
            log.warn("voice call returned {}", status);
            throw switch (status) {
                case 413 -> new ApiException(HttpStatus.CONTENT_TOO_LARGE, "FILE_TOO_LARGE", "음성은 10MB 이하로 보내 주세요.");
                case 415 -> new ApiException(HttpStatus.UNSUPPORTED_MEDIA_TYPE, "UNSUPPORTED_MEDIA_TYPE", "음성 파일을 읽을 수 없습니다. 다시 녹음해 주세요.");
                case 422 -> ApiException.invalid("audio", "too_long", "음성은 60초 이하로 녹음해 주세요.");
                case 503 -> unavailable();
                default -> upstream();
            };
        } catch (ResourceAccessException e) {
            log.warn("voice call failed: {}", e.getMessage());
            // 연결 시간 초과(HttpConnectTimeoutException)는 서비스가 없는 것이라 503
            if (e.getCause() instanceof java.net.http.HttpTimeoutException && !(e.getCause() instanceof java.net.http.HttpConnectTimeoutException)) {
                throw new ApiException(HttpStatus.GATEWAY_TIMEOUT, "UPSTREAM_TIMEOUT", "음성 인식이 지연되고 있습니다. 다시 시도해 주세요.");
            }
            throw unavailable(); // 음성 서비스를 띄우지 않은 구성(profile 미사용)
        } catch (RestClientException e) {
            log.warn("voice response unreadable: {}", e.getMessage());
            throw upstream();
        }
        if (data == null || !data.path("text").isString() || !data.path("duration_ms").isNumber()) throw upstream();
        return Api.ok(Map.of("text", data.path("text").asString(),
                "duration_ms", Math.min(Math.max(data.path("duration_ms").asInt(), 0), 60_000)));
    }

    private static ApiException unavailable() {
        return new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "SERVICE_UNAVAILABLE", "음성 질문을 사용할 수 없습니다. 텍스트로 질문해 주세요.");
    }

    private static ApiException upstream() {
        return new ApiException(HttpStatus.BAD_GATEWAY, "UPSTREAM_ERROR", "음성 인식 중 오류가 발생했습니다. 다시 시도해 주세요.");
    }
}
