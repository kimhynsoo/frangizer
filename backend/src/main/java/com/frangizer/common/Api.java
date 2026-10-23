package com.frangizer.common;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.context.request.RequestAttributes;
import org.springframework.web.context.request.RequestContextHolder;

/** 공통 응답 래퍼·시각 변환·페이지 조회. */
public final class Api {

    public static final ZoneId KST = ZoneId.of("Asia/Seoul");
    public static final String REQUEST_ID = "requestId";

    private Api() {
    }

    public static String requestId() {
        RequestAttributes attrs = RequestContextHolder.getRequestAttributes();
        Object id = attrs == null ? null : attrs.getAttribute(REQUEST_ID, RequestAttributes.SCOPE_REQUEST);
        return id == null ? newId("req") : id.toString();
    }

    public static String newId(String prefix) {
        return prefix + "-" + UUID.randomUUID();
    }

    public static Map<String, Object> ok(Object data) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("data", data);
        body.put("request_id", requestId());
        return body;
    }

    // ---- 시각: DB는 UTC, 응답은 KST 오프셋 ----

    public static OffsetDateTime kst(Instant instant) {
        return instant == null ? null : instant.atZone(KST).toOffsetDateTime();
    }

    public static OffsetDateTime kst(ResultSet rs, String column) throws SQLException {
        Timestamp ts = rs.getTimestamp(column);
        return ts == null ? null : kst(ts.toInstant());
    }

    public static Timestamp ts(Instant instant) {
        return instant == null ? null : Timestamp.from(instant);
    }

    public static Timestamp ts(OffsetDateTime time) {
        return time == null ? null : Timestamp.from(time.toInstant());
    }

    public static Timestamp now() {
        return Timestamp.from(Instant.now());
    }

    // ---- 검증 ----

    public static String oneOf(String field, String value, String... allowed) {
        if (value != null && !Arrays.asList(allowed).contains(value)) {
            throw ApiException.invalid(field, "invalid_enum", field + " 값이 올바르지 않습니다.");
        }
        return value;
    }

    // ---- 페이지 목록: { data, meta, request_id } ----

    public static <T> Map<String, Object> paged(JdbcClient db, String sql, Map<String, ?> params,
                                                int page, int pageSize, RowMapper<T> mapper) {
        Map<String, Object> body = new LinkedHashMap<>();
        PageResult<T> result = page(db, sql, params, page, pageSize, mapper);
        body.put("data", result.items());
        body.put("meta", result.meta());
        body.put("request_id", requestId());
        return body;
    }

    public record PageResult<T>(List<T> items, Map<String, Object> meta) {
    }

    /** sql은 ORDER BY까지 포함한 전체 조회문. */
    public static <T> PageResult<T> page(JdbcClient db, String sql, Map<String, ?> params,
                                         int page, int pageSize, RowMapper<T> mapper) {
        if (page < 1) throw ApiException.invalid("page", "out_of_range", "page는 1 이상이어야 합니다.");
        if (pageSize < 1 || pageSize > 100) {
            throw ApiException.invalid("page_size", "out_of_range", "page_size는 1~100이어야 합니다.");
        }
        long total = db.sql("SELECT COUNT(*) FROM (" + sql + ") t").params(params).query(Long.class).single();
        Map<String, Object> p = new HashMap<>(params);
        p.put("_limit", pageSize);
        p.put("_offset", (page - 1) * pageSize);
        List<T> items = db.sql(sql + " LIMIT :_limit OFFSET :_offset").params(p).query(mapper).list();
        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("page", page);
        meta.put("page_size", pageSize);
        meta.put("total", total);
        meta.put("total_pages", (total + pageSize - 1) / pageSize);
        return new PageResult<>(items, meta);
    }

    /** 메모리 목록 페이지(문서·판본처럼 건수가 적은 목록용). */
    public static Map<String, Object> slice(List<?> all, int page, int pageSize) {
        if (page < 1) throw ApiException.invalid("page", "out_of_range", "page는 1 이상이어야 합니다.");
        if (pageSize < 1 || pageSize > 100) {
            throw ApiException.invalid("page_size", "out_of_range", "page_size는 1~100이어야 합니다.");
        }
        int from = Math.min((page - 1) * pageSize, all.size());
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("data", all.subList(from, Math.min(from + pageSize, all.size())));
        body.put("meta", Map.of("page", page, "page_size", pageSize, "total", all.size(),
                "total_pages", (all.size() + pageSize - 1) / pageSize));
        body.put("request_id", requestId());
        return body;
    }
}
