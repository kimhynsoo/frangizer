package com.frangizer.rag;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;

import org.junit.jupiter.api.Test;

import com.frangizer.common.ApiException;
import com.sun.net.httpserver.HttpServer;

/** 실제 HTTP 응답 코드로 색인 실패를 영구(422)·일시(503)로 구분하는지 확인한다. */
class RagClientTest {

    static RagClient clientFor(int status, String body) throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(status, bytes.length);
            ex.getResponseBody().write(bytes);
            ex.close();
        });
        server.start();
        return new RagClient("http://127.0.0.1:" + server.getAddress().getPort(), "token");
    }

    @Test
    void validationErrorIsRejected() throws Exception {
        RagClient client = clientFor(422, """
                {"error":{"code":"VALIDATION_ERROR","message":"요청 형식을 확인해 주세요.","details":[],
                 "retryable":false},"request_id":"req-1"}""");
        assertThatThrownBy(() -> client.submitIndexJob(Map.of()))
                .isInstanceOf(RagClient.Rejected.class).hasMessage("요청 형식을 확인해 주세요.");
    }

    @Test
    void unavailableStaysRetryable() throws Exception {
        RagClient client = clientFor(503, "{}");
        assertThatThrownBy(() -> client.submitIndexJob(Map.of()))
                .isInstanceOf(ApiException.class).isNotInstanceOf(RagClient.Rejected.class);
    }
}
