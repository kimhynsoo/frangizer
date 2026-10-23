package com.frangizer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import com.sun.net.httpserver.HttpServer;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** POST /stt → 내부 음성 서비스 프록시. 음성 서비스는 JDK HttpServer로 흉내 낸다. */
@SpringBootTest
@ActiveProfiles("test")
@AutoConfigureMockMvc
class SttTest {

    static final AtomicReference<String> reply = new AtomicReference<>();
    static final AtomicReference<String> seenToken = new AtomicReference<>();
    static final HttpServer voice = start();

    static HttpServer start() {
        try {
            HttpServer s = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            s.createContext("/internal/v1/stt", ex -> {
                seenToken.set(ex.getRequestHeaders().getFirst("X-Internal-Token"));
                ex.getRequestBody().readAllBytes();
                String[] r = reply.get().split(" ", 2);
                byte[] b = r[1].getBytes(StandardCharsets.UTF_8);
                ex.getResponseHeaders().add("Content-Type", "application/json");
                ex.sendResponseHeaders(Integer.parseInt(r[0]), b.length);
                ex.getResponseBody().write(b);
                ex.close();
            });
            s.start();
            return s;
        } catch (java.io.IOException e) {
            throw new IllegalStateException(e);
        }
    }

    @DynamicPropertySource
    static void voiceUrl(DynamicPropertyRegistry r) {
        r.add("app.voice-url", () -> "http://127.0.0.1:" + voice.getAddress().getPort());
    }

    @AfterAll
    static void stop() {
        voice.stop(0);
    }

    @Autowired MockMvc mvc;
    @Autowired ObjectMapper json;

    @Test
    void proxiesToVoiceService() throws Exception {
        String token = json.readTree(mvc.perform(post("/api/v1/login").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"username\":\"demo-owner-a001\",\"password\":\"demo-password\"}"))
                .andReturn().getResponse().getContentAsString()).path("accessToken").asString();

        reply.set("200 {\"data\":{\"text\":\"바닐라라테 시럽 몇 미리야\",\"duration_ms\":2300}}");
        JsonNode ok = send(token, "audio/webm;codecs=opus", 200);
        assertThat(ok.at("/data/text").asString()).isEqualTo("바닐라라테 시럽 몇 미리야");
        assertThat(ok.at("/data/duration_ms").asInt()).isEqualTo(2300);
        assertThat(seenToken.get()).isEqualTo("test-internal-token");

        // 형식은 게이트웨이에서 먼저 거른다
        assertThat(send(token, "audio/mpeg", 415).at("/error/code").asString()).isEqualTo("UNSUPPORTED_MEDIA_TYPE");
        // 음성 서비스 판정(60초 초과, 모델 준비 중)을 공개 오류로 바꾼다
        reply.set("422 {\"error\":{\"code\":\"VALIDATION_ERROR\"}}");
        assertThat(send(token, "audio/wav", 422).at("/error/details/0/field").asString()).isEqualTo("audio");
        reply.set("503 {\"error\":{\"code\":\"SERVICE_UNAVAILABLE\"}}");
        assertThat(send(token, "audio/wav", 503).at("/error/retryable").asBoolean()).isTrue();
        // 토큰 없으면 401
        assertThat(mvc.perform(multipart("/api/v1/stt").file(audio("audio/wav"))).andReturn().getResponse().getStatus())
                .isEqualTo(401);
    }

    static MockMultipartFile audio(String type) {
        return new MockMultipartFile("audio", "q.webm", type, new byte[] {1, 2, 3});
    }

    JsonNode send(String token, String type, int expected) throws Exception {
        var res = mvc.perform(multipart("/api/v1/stt").file(audio(type)).header("Authorization", "Bearer " + token))
                .andReturn().getResponse();
        assertThat(res.getStatus()).isEqualTo(expected);
        return json.readTree(res.getContentAsString(StandardCharsets.UTF_8));
    }
}
