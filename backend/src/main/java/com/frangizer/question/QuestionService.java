package com.frangizer.question;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

import com.frangizer.auth.AuthUser;
import com.frangizer.common.Api;
import com.frangizer.common.ApiException;

import tools.jackson.databind.ObjectMapper;

@Service
public class QuestionService {

    private final JdbcClient db;
    private final ObjectMapper json;

    QuestionService(JdbcClient db, ObjectMapper json) {
        this.db = db;
        this.json = json;
    }

    /** question_events 행 → 명세 QuestionEvent. */
    public RowMapper<Map<String, Object>> eventMapper() {
        return (rs, n) -> {
            Map<String, Object> e = new LinkedHashMap<>();
            e.put("question_id", rs.getString("question_id"));
            e.put("conversation_id", rs.getString("conversation_id"));
            e.put("store_id", rs.getString("store_id"));
            e.put("store_name", rs.getString("store_name"));
            e.put("region_code", rs.getString("region_code"));
            e.put("region_name", rs.getString("region_name"));
            e.put("user_role", rs.getString("user_role"));
            e.put("created_at", Api.kst(rs, "created_at"));
            e.put("updated_at", Api.kst(rs, "updated_at"));
            e.put("question_redacted", rs.getString("question_redacted"));
            e.put("answer_status", rs.getString("answer_status"));
            e.put("resolution_status", rs.getString("resolution_status"));
            e.put("category", rs.getString("category"));
            e.put("topic_id", rs.getString("topic_id"));
            e.put("classification_status", rs.getString("classification_status"));
            e.put("classification_version", rs.getString("classification_version"));
            String result = rs.getString("result");
            e.put("result", result == null ? null : json.readTree(result));
            e.put("error_code", rs.getString("error_code"));
            e.put("attempt_count", rs.getInt("attempt_count"));
            e.put("revision", rs.getInt("revision"));
            return e;
        };
    }

    public Map<String, Object> conversation(AuthUser user, String conversationId) {
        List<Map<String, Object>> rows = db.sql(
                        "SELECT * FROM question_events WHERE conversation_id = :c ORDER BY created_at")
                .param("c", conversationId).query(eventMapper()).list();
        if (rows.isEmpty()) throw ApiException.notFound();
        String owner = db.sql("SELECT user_id FROM question_events WHERE conversation_id = :c LIMIT 1")
                .param("c", conversationId).query(String.class).single();
        if (!owner.equals(user.id())) throw ApiException.forbidden();

        Map<String, Object> conv = new LinkedHashMap<>();
        conv.put("id", conversationId);
        conv.put("created_at", rows.getFirst().get("created_at"));
        conv.put("updated_at", rows.stream().map(r -> (OffsetDateTime) r.get("updated_at"))
                .max(OffsetDateTime::compareTo).orElseThrow());
        conv.put("messages", rows.stream().map(r -> {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("question_id", r.get("question_id"));
            m.put("question_redacted", r.get("question_redacted"));
            m.put("created_at", r.get("created_at"));
            m.put("answer_status", r.get("answer_status"));
            m.put("result", r.get("result"));
            m.put("error_code", r.get("error_code"));
            return m;
        }).toList());
        return conv;
    }

    public Map<String, Object> feedback(AuthUser user, String questionId, boolean helpful) {
        Map<String, Object> q = db.sql("SELECT user_id, answer_status FROM question_events WHERE question_id = :q")
                .param("q", questionId).query().listOfRows().stream().findFirst()
                .orElseThrow(ApiException::notFound);
        if (!user.id().equals(q.get("user_id"))) throw ApiException.forbidden();
        String status = (String) q.get("answer_status");
        if (status.equals("processing") || status.equals("system_error")) {
            throw ApiException.conflict("INVALID_STATE", "답변이 완료된 질문에만 의견을 남길 수 있습니다.");
        }
        var now = Api.now();
        // 다시 보내면 덮어쓴다
        int updated = db.sql("UPDATE question_feedback SET helpful = :h, submitted_at = :t WHERE question_id = :q")
                .param("h", helpful).param("t", now).param("q", questionId).update();
        if (updated == 0) {
            db.sql("INSERT INTO question_feedback (question_id, helpful, submitted_at) VALUES (:q, :h, :t)")
                    .param("q", questionId).param("h", helpful).param("t", now).update();
        }
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("question_id", questionId);
        data.put("helpful", helpful);
        data.put("submitted_at", Api.kst(now.toInstant()));
        return data;
    }
}
