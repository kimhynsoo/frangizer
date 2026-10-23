package com.frangizer.common;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Map;
import java.util.Optional;
import java.util.function.Supplier;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import tools.jackson.databind.ObjectMapper;

/**
 * Idempotency-Key 처리. 같은 사용자·경로·키·본문 → 저장된 응답 재사용, 본문이 다르면 409.
 * 키는 24시간 보존한다.
 */
@Component
public class Idempotency {

    public record Entry(String requestHash, String resourceId, Integer statusCode, String response) {
    }

    private final JdbcClient db;
    private final ObjectMapper json;

    Idempotency(JdbcClient db, ObjectMapper json) {
        this.db = db;
        this.json = json;
    }

    public static void validateKey(String key) {
        if (key == null || key.length() < 8 || key.length() > 100) {
            throw ApiException.invalid("Idempotency-Key", "invalid", "Idempotency-Key는 8~100자여야 합니다.");
        }
    }

    public String hash(Object request) {
        return sha256(json.writeValueAsBytes(request));
    }

    public static String sha256(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    public Optional<Entry> find(String userId, String operation, String key) {
        db.sql("DELETE FROM idempotency_keys WHERE created_at < :cutoff")
                .param("cutoff", Api.ts(Instant.now().minus(Duration.ofHours(24)))).update();
        return db.sql("SELECT request_hash, resource_id, status_code, response FROM idempotency_keys "
                        + "WHERE user_id = :u AND operation = :op AND idem_key = :k")
                .params(Map.of("u", userId, "op", operation, "k", key))
                .query((rs, n) -> new Entry(rs.getString(1), rs.getString(2),
                        (Integer) rs.getObject(3), rs.getString(4)))
                .optional();
    }

    /** 처리 중 표시(response NULL)로 먼저 기록. 동시 요청이 이미 기록했으면 409. */
    public void begin(String userId, String operation, String key, String requestHash, String resourceId) {
        try {
            db.sql("INSERT INTO idempotency_keys (user_id, operation, idem_key, request_hash, resource_id, created_at) "
                            + "VALUES (:u, :op, :k, :h, :r, :now)")
                    .param("u", userId).param("op", operation).param("k", key).param("h", requestHash)
                    .param("r", resourceId).param("now", Api.now())
                    .update();
        } catch (DuplicateKeyException e) {
            throw inProgress();
        }
    }

    public void complete(String userId, String operation, String key, int status, Object response) {
        db.sql("UPDATE idempotency_keys SET status_code = :s, response = :r "
                        + "WHERE user_id = :u AND operation = :op AND idem_key = :k")
                .param("s", status).param("r", json.writeValueAsString(response))
                .param("u", userId).param("op", operation).param("k", key)
                .update();
    }

    public void release(String userId, String operation, String key) {
        db.sql("DELETE FROM idempotency_keys WHERE user_id = :u AND operation = :op AND idem_key = :k")
                .param("u", userId).param("op", operation).param("k", key)
                .update();
    }

    public ResponseEntity<Object> replay(Entry entry) {
        tools.jackson.databind.JsonNode body = json.readTree(entry.response());
        if (body instanceof tools.jackson.databind.node.ObjectNode node) node.put("request_id", Api.requestId());
        return ResponseEntity.status(entry.statusCode()).body(body);
    }

    /** 일반 변경 API용: 성공 응답만 저장한다. 실패하면 키를 풀어 같은 키로 다시 시도할 수 있다. */
    public ResponseEntity<Object> run(String userId, String operation, String key, Object request,
                                      Supplier<ResponseEntity<Object>> action) {
        validateKey(key);
        String hash = hash(request);
        Optional<Entry> existing = find(userId, operation, key);
        if (existing.isPresent()) {
            Entry e = existing.get();
            if (!e.requestHash().equals(hash)) throw conflict();
            if (e.response() == null) throw inProgress();
            return replay(e);
        }
        begin(userId, operation, key, hash, null);
        try {
            ResponseEntity<Object> response = action.get();
            complete(userId, operation, key, response.getStatusCode().value(), response.getBody());
            return response;
        } catch (RuntimeException e) {
            release(userId, operation, key);
            throw e;
        }
    }

    public static ApiException conflict() {
        return ApiException.conflict("IDEMPOTENCY_CONFLICT", "같은 Idempotency-Key로 다른 요청을 보냈습니다.");
    }

    private static ApiException inProgress() {
        return new ApiException(org.springframework.http.HttpStatus.CONFLICT, "IDEMPOTENCY_CONFLICT",
                "같은 요청을 처리하고 있습니다. 잠시 후 같은 키로 다시 시도해 주세요.", java.util.List.of(), true);
    }
}
