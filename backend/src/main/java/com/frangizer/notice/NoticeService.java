package com.frangizer.notice;

import java.time.Instant;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

import com.frangizer.auth.AuthUser;
import com.frangizer.common.Api;
import com.frangizer.common.ApiException;

@Service
public class NoticeService {

    private final JdbcClient db;

    NoticeService(JdbcClient db) {
        this.db = db;
    }

    /** owner는 소속 점포가 대상인 공지, hq는 브랜드 전체 공지(확인 시각은 null). */
    public Map<String, Object> list(AuthUser user, boolean unread, int page, int pageSize) {
        boolean owner = "owner".equals(user.role());
        if (!owner && !user.isHq()) throw ApiException.forbidden();
        Map<String, Object> p = new HashMap<>();
        p.put("b", user.brandId());
        p.put("s", owner ? user.storeId() : "");
        String sql = """
                SELECT n.notice_id, n.document_id, n.version_id, n.title, n.published_at,
                       v.change_summary, v.effective_from, a.acknowledged_at
                FROM notices n
                JOIN document_versions v ON v.version_id = n.version_id
                LEFT JOIN notice_acknowledgements a ON a.notice_id = n.notice_id AND a.store_id = :s
                WHERE n.brand_id = :b"""
                + (owner ? " AND EXISTS (SELECT 1 FROM notice_targets t WHERE t.notice_id = n.notice_id AND t.store_id = :s)" : "")
                + (unread ? " AND a.acknowledged_at IS NULL" : "")
                + " ORDER BY n.published_at DESC, n.notice_id";
        return Api.paged(db, sql, p, page, pageSize, (rs, n) -> {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("notice_id", rs.getString("notice_id"));
            m.put("document_id", rs.getString("document_id"));
            m.put("version_id", rs.getString("version_id"));
            m.put("title", rs.getString("title"));
            m.put("change_summary", rs.getString("change_summary"));
            m.put("effective_from", Api.kst(rs, "effective_from"));
            m.put("published_at", Api.kst(rs, "published_at"));
            m.put("acknowledged_at", Api.kst(rs, "acknowledged_at"));
            m.put("detail_url", "/api/v1/documents/" + rs.getString("document_id") + "/versions/" + rs.getString("version_id"));
            return m;
        });
    }

    /** 점포는 토큰에서 결정한다. 재요청이면 기존 기록을 그대로 돌려준다. */
    public Map<String, Object> acknowledge(AuthUser user, String noticeId) {
        if (!"owner".equals(user.role()) || user.storeId() == null) throw ApiException.forbidden();
        requireNotice(user, noticeId);
        boolean target = db.sql("SELECT COUNT(*) FROM notice_targets WHERE notice_id = :n AND store_id = :s")
                .param("n", noticeId).param("s", user.storeId()).query(Long.class).single() > 0;
        if (!target) throw ApiException.forbidden();
        try {
            db.sql("INSERT INTO notice_acknowledgements (notice_id, store_id, acknowledged_by, acknowledged_at) "
                            + "VALUES (:n, :s, :u, :t)")
                    .param("n", noticeId).param("s", user.storeId()).param("u", user.id()).param("t", Api.now())
                    .update();
        } catch (DuplicateKeyException alreadyAcknowledged) {
            // 멱등: 기존 기록 반환
        }
        return db.sql("SELECT * FROM notice_acknowledgements WHERE notice_id = :n AND store_id = :s")
                .param("n", noticeId).param("s", user.storeId())
                .query((rs, n) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("notice_id", rs.getString("notice_id"));
                    m.put("store_id", rs.getString("store_id"));
                    m.put("acknowledged_by", rs.getString("acknowledged_by"));
                    m.put("acknowledged_at", Api.kst(rs, "acknowledged_at"));
                    return m;
                }).single();
    }

    /** summary는 필터와 무관하게 배포 시 고정된 전체 대상 기준. data·meta만 필터를 따른다. */
    public Map<String, Object> acknowledgements(AuthUser user, String noticeId, Boolean acknowledged, String regionCode,
                                                int page, int pageSize) {
        user.requireHq("analytics:read");
        requireNotice(user, noticeId);

        Map<String, Object> counts = db.sql("""
                        SELECT COUNT(*) AS target_count, COUNT(a.store_id) AS acknowledged_count
                        FROM notice_targets t
                        LEFT JOIN notice_acknowledgements a ON a.notice_id = t.notice_id AND a.store_id = t.store_id
                        WHERE t.notice_id = :n""")
                .param("n", noticeId).query().singleRow();
        long targets = ((Number) counts.get("target_count")).longValue();
        long acked = ((Number) counts.get("acknowledged_count")).longValue();
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("notice_id", noticeId);
        summary.put("as_of", Api.kst(Instant.now()));
        summary.put("target_count", targets);
        summary.put("acknowledged_count", acked);
        summary.put("unacknowledged_count", targets - acked);
        summary.put("acknowledgement_rate", targets == 0 ? null : (double) acked / targets);

        Map<String, Object> p = new HashMap<>();
        p.put("n", noticeId);
        if (regionCode != null) p.put("r", regionCode);
        String sql = """
                SELECT t.store_id, s.name AS store_name, s.region_code, a.acknowledged_at, a.acknowledged_by
                FROM notice_targets t
                JOIN stores s ON s.store_id = t.store_id
                LEFT JOIN notice_acknowledgements a ON a.notice_id = t.notice_id AND a.store_id = t.store_id
                WHERE t.notice_id = :n"""
                + (regionCode != null ? " AND s.region_code = :r" : "")
                + (acknowledged == null ? "" : acknowledged ? " AND a.store_id IS NOT NULL" : " AND a.store_id IS NULL")
                + " ORDER BY t.store_id";
        Api.PageResult<Map<String, Object>> result = Api.page(db, sql, p, page, pageSize, (rs, n) -> {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("store_id", rs.getString("store_id"));
            m.put("store_name", rs.getString("store_name"));
            m.put("region_code", rs.getString("region_code"));
            m.put("acknowledged_at", Api.kst(rs, "acknowledged_at"));
            m.put("acknowledged_by", rs.getString("acknowledged_by"));
            return m;
        });
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("data", result.items());
        body.put("summary", summary);
        body.put("meta", result.meta());
        body.put("request_id", Api.requestId());
        return body;
    }

    private void requireNotice(AuthUser user, String noticeId) {
        String brand = db.sql("SELECT brand_id FROM notices WHERE notice_id = :n").param("n", noticeId)
                .query(String.class).optional().orElseThrow(ApiException::notFound);
        if (!brand.equals(user.brandId())) throw ApiException.notFound();
    }
}
