package com.frangizer.analytics;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.DayOfWeek;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.time.temporal.TemporalAdjusters;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

import com.frangizer.auth.AuthUser;
import com.frangizer.common.Api;
import com.frangizer.common.ApiException;
import com.frangizer.question.QuestionService;

/** 본사 문의 집계. 숫자는 모두 SQL로 계산한다(LLM에 맡기지 않음). DB는 UTC, 경계는 KST. */
@Service
public class AnalyticsService {

    /** ponytail: 표본 적음 기준은 고정값. 기획에서 바꾸면 여기만 고친다. */
    static final int SMALL_SAMPLE = 5;
    private static final String[] CATEGORIES = {"franchise", "recipe", "hygiene", "consumer", "other"};
    private static final String METRICS = """
            COUNT(*) AS total,
            SUM(CASE WHEN q.answer_status = 'processing' THEN 1 ELSE 0 END) AS processing_count,
            SUM(CASE WHEN q.answer_status = 'answered' THEN 1 ELSE 0 END) AS answered_count,
            SUM(CASE WHEN q.answer_status = 'needs_review' THEN 1 ELSE 0 END) AS needs_review_count,
            SUM(CASE WHEN q.answer_status = 'handoff' THEN 1 ELSE 0 END) AS handoff_count,
            SUM(CASE WHEN q.answer_status = 'system_error' THEN 1 ELSE 0 END) AS system_error_count,
            COUNT(DISTINCT q.store_id) AS active_store_count,
            SUM(CASE WHEN q.classification_status = 'pending' THEN 1 ELSE 0 END) AS classification_pending_count""";

    private final JdbcClient db;
    private final QuestionService questions;

    AnalyticsService(JdbcClient db, QuestionService questions) {
        this.db = db;
        this.questions = questions;
    }

    public record Query(String weekStart, String from, String to, String regionCode, String storeId,
                        String category, String topicId, String userRole) {
    }

    record Period(Instant start, Instant end, boolean partial, Instant cmpStart, Instant cmpEnd) {
    }

    // ---------------------------------------------------------------- 필터 선택지

    public Map<String, Object> regions(AuthUser user, int page, int pageSize) {
        user.requireHq("analytics:read");
        return Api.paged(db, "SELECT code, name FROM regions ORDER BY code", Map.of(), page, pageSize,
                (rs, n) -> Map.of("code", rs.getString("code"), "name", rs.getString("name")));
    }

    public Map<String, Object> stores(AuthUser user, String regionCode, int page, int pageSize) {
        user.requireHq("analytics:read");
        Map<String, Object> p = new HashMap<>(Map.of("b", user.brandId()));
        if (regionCode != null) p.put("r", regionCode);
        return Api.paged(db, "SELECT * FROM stores WHERE brand_id = :b" + (regionCode != null ? " AND region_code = :r" : "")
                        + " ORDER BY store_id", p, page, pageSize,
                (rs, n) -> Map.of("id", rs.getString("store_id"), "name", rs.getString("name"),
                        "region_code", rs.getString("region_code"), "active", rs.getBoolean("active")));
    }

    public Map<String, Object> topics(AuthUser user, String category, int page, int pageSize) {
        user.requireHq("analytics:read");
        Api.oneOf("category", category, CATEGORIES);
        Map<String, Object> p = new HashMap<>();
        if (category != null) p.put("c", category);
        return Api.paged(db, "SELECT * FROM topics" + (category != null ? " WHERE category = :c" : "") + " ORDER BY id",
                p, page, pageSize,
                (rs, n) -> Map.of("id", rs.getString("id"), "label", rs.getString("label"),
                        "category", rs.getString("category"), "active", rs.getBoolean("active")));
    }

    // ---------------------------------------------------------------- 집계

    public Map<String, Object> questions(AuthUser user, Query query) {
        user.requireHq("analytics:read");
        Period period = period(query, Instant.now());
        Map<String, Object> p = new HashMap<>();
        String filter = filters(user, query, p);

        Map<String, Object> current = metricsFor(filter, p, period.start(), period.end());
        Map<String, Object> previous = metricsFor(filter, p, period.cmpStart(), period.cmpEnd());
        long cur = (long) current.get("total");
        long prev = (long) previous.get("total");

        Map<String, Object> pp = withRange(p, period.start(), period.end());
        String where = " FROM question_events q WHERE " + filter;
        List<Map<String, Object>> topTopics = db.sql("""
                        SELECT q.topic_id, MAX(t.label) AS label, COUNT(*) AS cnt,
                          SUM(CASE WHEN q.answer_status IN ('needs_review', 'handoff') THEN 1 ELSE 0 END) AS review_cnt
                        FROM question_events q LEFT JOIN topics t ON t.id = q.topic_id
                        """ + "WHERE " + filter + " AND q.topic_id IS NOT NULL"
                        + " GROUP BY q.topic_id ORDER BY cnt DESC, q.topic_id LIMIT 10")
                .params(pp)
                .query((rs, n) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("topic_id", rs.getString("topic_id"));
                    m.put("label", rs.getString("label") != null ? rs.getString("label") : rs.getString("topic_id"));
                    m.put("count", rs.getLong("cnt"));
                    m.put("review_required_count", rs.getLong("review_cnt"));
                    return m;
                }).list();
        List<Map<String, Object>> byCategory = db.sql("SELECT q.category, COUNT(*) AS cnt" + where
                        + " GROUP BY q.category ORDER BY cnt DESC, q.category")
                .params(pp)
                .query((rs, n) -> Map.<String, Object>of("category", rs.getString("category"), "count", rs.getLong("cnt")))
                .list();
        List<Map<String, Object>> byRegion = db.sql("SELECT COALESCE(q.region_code, 'UNASSIGNED') AS rc, "
                        + "MAX(COALESCE(q.region_name, '미분류')) AS rn, " + METRICS + where
                        + " GROUP BY COALESCE(q.region_code, 'UNASSIGNED') ORDER BY rc")
                .params(pp)
                .query((rs, n) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    Map<String, Object> metrics = metrics(rs);
                    m.put("region_code", rs.getString("rc"));
                    m.put("region_name", rs.getString("rn"));
                    m.put("metrics", metrics);
                    m.put("small_sample", (long) metrics.get("total") < SMALL_SAMPLE);
                    return m;
                }).list();

        Map<String, Object> periodOut = new LinkedHashMap<>();
        periodOut.put("start", Api.kst(period.start()));
        periodOut.put("end_exclusive", Api.kst(period.end()));
        periodOut.put("timezone", "Asia/Seoul");
        periodOut.put("is_partial", period.partial());
        periodOut.put("comparison_start", Api.kst(period.cmpStart()));
        periodOut.put("comparison_end_exclusive", Api.kst(period.cmpEnd()));
        Map<String, Object> filtersOut = new LinkedHashMap<>();
        filtersOut.put("region_code", query.regionCode());
        filtersOut.put("store_id", query.storeId());
        filtersOut.put("category", query.category());
        filtersOut.put("user_role", p.get("role"));
        filtersOut.put("topic_id", query.topicId());

        Map<String, Object> data = new LinkedHashMap<>();
        data.put("period", periodOut);
        data.put("filters", filtersOut);
        data.put("generated_at", Api.kst(Instant.now()));
        data.put("metrics", current);
        data.put("previous_period", previous);
        data.put("total_change", cur - prev);
        data.put("total_change_rate", prev == 0 ? null : round((double) (cur - prev) / prev));
        data.put("change_kind", prev == 0 ? (cur == 0 ? "no_data" : "new")
                : cur > prev ? "increase" : cur < prev ? "decrease" : "unchanged");
        data.put("top_topics", topTopics);
        data.put("by_category", byCategory);
        data.put("by_region", byRegion);
        return data;
    }

    private Map<String, Object> metricsFor(String filter, Map<String, Object> p, Instant start, Instant end) {
        return db.sql("SELECT " + METRICS + " FROM question_events q WHERE " + filter)
                .params(withRange(p, start, end))
                .query((rs, n) -> metrics(rs)).single();
    }

    private static Map<String, Object> withRange(Map<String, Object> p, Instant start, Instant end) {
        Map<String, Object> out = new HashMap<>(p);
        out.put("start", Api.ts(start));
        out.put("end", Api.ts(end));
        return out;
    }

    private static Map<String, Object> metrics(ResultSet rs) throws SQLException {
        long total = rs.getLong("total");
        long answered = rs.getLong("answered_count");
        long needsReview = rs.getLong("needs_review_count");
        long handoff = rs.getLong("handoff_count");
        long review = needsReview + handoff;
        long completed = answered + review;
        long stores = rs.getLong("active_store_count");
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("total", total);
        m.put("processing_count", rs.getLong("processing_count"));
        m.put("answered_count", answered);
        m.put("review_required_count", review);
        m.put("needs_review_count", needsReview);
        m.put("handoff_count", handoff);
        m.put("system_error_count", rs.getLong("system_error_count"));
        m.put("completed_count", completed);
        m.put("active_store_count", stores);
        m.put("classification_pending_count", rs.getLong("classification_pending_count"));
        m.put("guidance_rate", completed == 0 ? null : round((double) answered / completed));
        m.put("review_required_rate", completed == 0 ? null : round((double) review / completed));
        m.put("questions_per_active_store", stores == 0 ? null : round((double) total / stores));
        return m;
    }

    private static double round(double v) {
        return BigDecimal.valueOf(v).setScale(3, RoundingMode.HALF_UP).doubleValue();
    }

    /** 기본: 지난 완료 주(KST 월 00:00 ~ 다음 월 00:00). 진행 중 주는 직전 주의 같은 요일·시각까지와 비교. */
    static Period period(Query q, Instant now) {
        if (q.weekStart() != null && (q.from() != null || q.to() != null)) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "MALFORMED_REQUEST", "week_start와 from/to는 함께 쓸 수 없습니다.");
        }
        if ((q.from() == null) != (q.to() == null)) {
            String missing = q.from() == null ? "from" : "to";
            throw ApiException.invalid(missing, "required", "from과 to는 함께 지정해야 합니다.");
        }
        if (q.from() != null) {
            Instant from = parseTime("from", q.from());
            Instant to = parseTime("to", q.to());
            if (!from.isBefore(to)) throw ApiException.invalid("to", "invalid_range", "to는 from보다 뒤여야 합니다.");
            if (Duration.between(from, to).compareTo(Duration.ofDays(31)) > 0) {
                throw ApiException.invalid("to", "too_long", "조회 기간은 최대 31일입니다.");
            }
            if (to.isAfter(now)) throw ApiException.invalid("to", "future", "to는 미래일 수 없습니다.");
            Duration length = Duration.between(from, to);
            return new Period(from, to, false, from.minus(length), from);
        }
        LocalDate monday;
        if (q.weekStart() != null) {
            try {
                monday = LocalDate.parse(q.weekStart());
            } catch (DateTimeParseException e) {
                throw ApiException.invalid("week_start", "invalid", "week_start는 YYYY-MM-DD 형식이어야 합니다.");
            }
            if (monday.getDayOfWeek() != DayOfWeek.MONDAY) {
                throw ApiException.invalid("week_start", "not_monday", "week_start는 월요일이어야 합니다.");
            }
        } else {
            monday = LocalDate.ofInstant(now, Api.KST).with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY)).minusWeeks(1);
        }
        Instant start = monday.atStartOfDay(Api.KST).toInstant();
        Instant end = start.plus(Duration.ofDays(7));
        if (start.isAfter(now)) throw ApiException.invalid("week_start", "future", "아직 시작하지 않은 주입니다.");
        boolean partial = end.isAfter(now);
        Instant cmpStart = start.minus(Duration.ofDays(7));
        return new Period(start, end, partial, cmpStart, partial ? now.minus(Duration.ofDays(7)) : start);
    }

    private static Instant parseTime(String field, String value) {
        try {
            return OffsetDateTime.parse(value).toInstant();
        } catch (DateTimeParseException e) {
            throw ApiException.invalid(field, "invalid", field + "는 시간대를 포함한 RFC 3339 시각이어야 합니다.");
        }
    }

    /** 공통 필터 WHERE 절(:start, :end는 호출 쪽에서 채운다). 브랜드 범위 밖이면 403. */
    private String filters(AuthUser user, Query q, Map<String, Object> p) {
        String role = q.userRole() == null ? "owner" : Api.oneOf("user_role", q.userRole(), "owner", "prospect");
        Api.oneOf("category", q.category(), CATEGORIES);
        StringBuilder sql = new StringBuilder(
                "q.brand_id = :b AND q.user_role = :role AND q.created_at >= :start AND q.created_at < :end");
        p.put("b", user.brandId());
        p.put("role", role);
        if (q.regionCode() != null) {
            boolean known = "UNASSIGNED".equals(q.regionCode()) || db.sql(
                            "SELECT COUNT(*) FROM stores WHERE brand_id = :b AND region_code = :r")
                    .param("b", user.brandId()).param("r", q.regionCode()).query(Long.class).single() > 0;
            if (!known) throw ApiException.forbidden();
            sql.append(" AND COALESCE(q.region_code, 'UNASSIGNED') = :region");
            p.put("region", q.regionCode());
        }
        if (q.storeId() != null) {
            boolean own = db.sql("SELECT COUNT(*) FROM stores WHERE brand_id = :b AND store_id = :s")
                    .param("b", user.brandId()).param("s", q.storeId()).query(Long.class).single() > 0;
            if (!own) throw ApiException.forbidden();
            sql.append(" AND q.store_id = :store");
            p.put("store", q.storeId());
        }
        if (q.category() != null) {
            sql.append(" AND q.category = :category");
            p.put("category", q.category());
        }
        if (q.topicId() != null) {
            requireTopic(q.topicId());
            sql.append(" AND q.topic_id = :topic");
            p.put("topic", q.topicId());
        }
        return sql.toString();
    }

    private void requireTopic(String topicId) {
        boolean exists = db.sql("SELECT COUNT(*) FROM topics WHERE id = :t").param("t", topicId)
                .query(Long.class).single() > 0;
        if (!exists) throw ApiException.invalid("topic_id", "unknown", "등록되지 않은 주제입니다.");
    }

    // ---------------------------------------------------------------- 문의 목록·상세·수정

    public Map<String, Object> events(AuthUser user, Query query, String answerStatus, String resolutionStatus,
                                      String classificationStatus, int page, int pageSize) {
        user.requireHq("analytics:read");
        Period period = period(query, Instant.now());
        Map<String, Object> p = new HashMap<>();
        StringBuilder sql = new StringBuilder("SELECT q.* FROM question_events q WHERE " + filters(user, query, p));
        if (Api.oneOf("answer_status", answerStatus, "processing", "answered", "needs_review", "handoff", "system_error") != null) {
            sql.append(" AND q.answer_status = :as");
            p.put("as", answerStatus);
        }
        if (Api.oneOf("resolution_status", resolutionStatus, "unknown", "open", "in_progress", "closed") != null) {
            sql.append(" AND q.resolution_status = :rs");
            p.put("rs", resolutionStatus);
        }
        if (Api.oneOf("classification_status", classificationStatus, "pending", "classified", "failed", "corrected") != null) {
            sql.append(" AND q.classification_status = :cs");
            p.put("cs", classificationStatus);
        }
        sql.append(" ORDER BY q.created_at DESC, q.question_id");
        return Api.paged(db, sql.toString(), withRange(p, period.start(), period.end()), page, pageSize,
                questions.eventMapper());
    }

    public Map<String, Object> event(AuthUser user, String questionId) {
        user.requireHq("analytics:read");
        return db.sql("SELECT * FROM question_events WHERE question_id = :q AND brand_id = :b")
                .param("q", questionId).param("b", user.brandId())
                .query(questions.eventMapper()).optional().orElseThrow(ApiException::notFound);
    }

    private static final Set<String> PATCH_FIELDS =
            Set.of("expected_revision", "category", "topic_id", "resolution_status", "review_note");

    /** 낙관적 잠금. 분류를 고치면 corrected. answer_status 원본은 바꾸지 않는다. */
    public Map<String, Object> patch(AuthUser user, String questionId, Map<String, Object> body) {
        user.requireHq("analytics:write");
        for (String key : body.keySet()) {
            if (!PATCH_FIELDS.contains(key)) throw ApiException.invalid(key, "not_allowed", "허용되지 않은 필드입니다: " + key);
        }
        if (!(body.get("expected_revision") instanceof Integer revision) || revision < 1) {
            throw ApiException.invalid("expected_revision", "required", "expected_revision(1 이상 정수)이 필요합니다.");
        }
        if (body.size() < 2) throw ApiException.invalid("body", "too_few", "바꿀 항목을 하나 이상 보내 주세요.");

        Map<String, Object> p = new HashMap<>();
        StringBuilder set = new StringBuilder("revision = revision + 1, updated_at = :now, updated_by = :u");
        p.put("now", Api.now());
        p.put("u", user.id());
        if (body.containsKey("category")) {
            if (!(body.get("category") instanceof String c)) throw ApiException.invalid("category", "invalid", "category가 올바르지 않습니다.");
            Api.oneOf("category", c, CATEGORIES);
            set.append(", category = :category");
            p.put("category", c);
        }
        if (body.containsKey("topic_id")) {
            Object t = body.get("topic_id");
            if (t != null && !(t instanceof String)) throw ApiException.invalid("topic_id", "invalid", "topic_id가 올바르지 않습니다.");
            if (t != null) requireTopic((String) t);
            set.append(", topic_id = :topic");
            p.put("topic", t);
        }
        if (body.containsKey("category") || body.containsKey("topic_id")) {
            set.append(", classification_status = 'corrected', classification_version = 'manual'");
        }
        if (body.containsKey("resolution_status")) {
            if (!(body.get("resolution_status") instanceof String r)) {
                throw ApiException.invalid("resolution_status", "invalid", "resolution_status가 올바르지 않습니다.");
            }
            Api.oneOf("resolution_status", r, "unknown", "open", "in_progress", "closed");
            set.append(", resolution_status = :resolution");
            p.put("resolution", r);
        }
        if (body.containsKey("review_note")) {
            if (!(body.get("review_note") instanceof String note) || note.length() > 2000) {
                throw ApiException.invalid("review_note", "invalid", "review_note는 2,000자 이하 문자열이어야 합니다.");
            }
            set.append(", review_note = :note");
            p.put("note", note);
        }
        p.put("q", questionId);
        p.put("b", user.brandId());
        p.put("rev", revision);
        int n = db.sql("UPDATE question_events SET " + set + " WHERE question_id = :q AND brand_id = :b AND revision = :rev")
                .params(p).update();
        if (n == 0) {
            event(user, questionId); // 없으면 404
            throw ApiException.conflict("INVALID_STATE", "다른 담당자가 먼저 수정했습니다. 새로고침 후 다시 시도해 주세요.");
        }
        return event(user, questionId);
    }
}
