package com.frangizer.document;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

import org.apache.pdfbox.Loader;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;

import com.frangizer.auth.AuthUser;
import com.frangizer.common.Api;
import com.frangizer.common.ApiException;
import com.frangizer.common.Idempotency;
import com.frangizer.document.dto.UploadMetadata;
import com.frangizer.rag.RagClient;
import com.frangizer.rag.RagService;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validator;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.exc.UnrecognizedPropertyException;

/** 문서·판본: 등록(draft) → 승인(approved, 색인 시작) → 배포(published, 공지 생성). */
@Service
public class DocumentService {

    private static final Logger log = LoggerFactory.getLogger(DocumentService.class);
    private static final long MAX_FILE_BYTES = 10L * 1024 * 1024;
    private static final String VERSION_SQL =
            "SELECT v.*, d.brand_id FROM document_versions v JOIN documents d ON d.document_id = v.document_id ";

    private final JdbcClient db;
    private final ObjectMapper json;
    private final Validator validator;
    private final Idempotency idem;
    private final RagClient rag;
    private final TransactionTemplate tx;
    private final Path storeDir;
    private final Path indexDir;

    DocumentService(JdbcClient db, ObjectMapper json, Validator validator, Idempotency idem, RagClient rag,
                    TransactionTemplate tx, @Value("${app.document-store-dir}") String storeDir,
                    @Value("${app.index-dir}") String indexDir) {
        this.db = db;
        this.json = json;
        this.validator = validator;
        this.idem = idem;
        this.rag = rag;
        this.tx = tx;
        this.storeDir = Path.of(storeDir);
        this.indexDir = Path.of(indexDir);
    }

    // ---------------------------------------------------------------- 조회

    private RowMapper<Map<String, Object>> versionMapper() {
        return (rs, n) -> {
            Map<String, Object> v = new LinkedHashMap<>();
            String docId = rs.getString("document_id");
            String verId = rs.getString("version_id");
            v.put("document_id", docId);
            v.put("version_id", verId);
            v.put("brand_id", rs.getString("brand_id"));
            v.put("title", rs.getString("title"));
            v.put("version", rs.getString("version"));
            v.put("category", rs.getString("category"));
            v.put("material_type", rs.getString("material_type"));
            v.put("is_demo", rs.getBoolean("is_demo"));
            v.put("visibility", rs.getString("visibility"));
            v.put("effective_from", Api.kst(rs, "effective_from"));
            v.put("lifecycle", rs.getString("lifecycle"));
            v.put("index_status", rs.getString("index_status"));
            v.put("created_at", Api.kst(rs, "created_at"));
            v.put("approved_at", Api.kst(rs, "approved_at"));
            v.put("published_at", Api.kst(rs, "published_at"));
            v.put("change_summary", rs.getString("change_summary"));
            v.put("original_source", json.readTree(rs.getString("original_source")));
            v.put("indexing_policy", json.readTree(rs.getString("indexing_policy")));
            v.put("file_name", rs.getString("file_name"));
            v.put("media_type", rs.getString("media_type"));
            v.put("file_size_bytes", rs.getLong("file_size_bytes"));
            v.put("sha256", rs.getString("sha256"));
            v.put("page_count", rs.getObject("page_count") == null ? null : rs.getInt("page_count"));
            v.put("file_url", "/api/v1/documents/" + docId + "/versions/" + verId + "/file");
            v.put("index_revision", rs.getString("index_revision"));
            // 응답에 내보내지 않는 내부 값
            v.put("_effective_until", Api.kst(rs, "effective_until"));
            v.put("_storage_key", rs.getString("storage_key"));
            return v;
        };
    }

    private static Map<String, Object> visible(Map<String, Object> v) {
        Map<String, Object> out = new LinkedHashMap<>(v);
        out.keySet().removeIf(k -> k.startsWith("_"));
        return out;
    }

    /** 다른 브랜드 문서는 존재 자체를 숨긴다(404). 권한 밖 공개 범위·미배포 판본은 403. */
    private Map<String, Object> loadVersion(AuthUser user, String docId, String verId) {
        Map<String, Object> v = db.sql(VERSION_SQL + "WHERE v.document_id = :d AND v.version_id = :v")
                .param("d", docId).param("v", verId).query(versionMapper()).optional()
                .orElseThrow(ApiException::notFound);
        if (!user.brandId().equals(v.get("brand_id"))) throw ApiException.notFound();
        if (!canRead(user, v)) throw ApiException.forbidden();
        return v;
    }

    private static boolean canRead(AuthUser user, Map<String, Object> v) {
        if (!user.canSee((String) v.get("visibility"))) return false;
        return "published".equals(v.get("lifecycle")) || user.canManageDocuments();
    }

    private List<Map<String, Object>> brandVersions(AuthUser user, String docId) {
        return db.sql(VERSION_SQL + "WHERE d.brand_id = :b" + (docId == null ? "" : " AND v.document_id = :d")
                        + " ORDER BY v.created_at DESC")
                .params(docId == null ? Map.of("b", user.brandId()) : Map.of("b", user.brandId(), "d", docId))
                .query(versionMapper()).list();
    }

    public Map<String, Object> list(AuthUser user, String category, OffsetDateTime asOfParam, boolean management,
                                    int page, int pageSize) {
        Api.oneOf("category", category, "franchise", "recipe", "hygiene", "consumer", "other");
        if (management && !user.canManageDocuments()) throw ApiException.forbidden();
        OffsetDateTime asOf = asOfParam == null ? Api.kst(Instant.now()) : asOfParam;

        Map<String, List<Map<String, Object>>> byDoc = brandVersions(user, null).stream()
                .collect(Collectors.groupingBy(v -> (String) v.get("document_id"), LinkedHashMap::new, Collectors.toList()));
        List<Map<String, Object>> items = new ArrayList<>();
        for (List<Map<String, Object>> versions : byDoc.values()) {
            // 관리 화면은 가장 최근 등록 판본, 일반 화면은 as_of에 유효한 배포 판본
            Map<String, Object> selected = management ? versions.getFirst() : versions.stream()
                    .filter(v -> "published".equals(v.get("lifecycle")) && user.canSee((String) v.get("visibility")))
                    .filter(v -> effectiveAt(v, asOf))
                    .findFirst().orElse(null);
            if (selected == null) continue;
            if (category != null && !category.equals(selected.get("category"))) continue;
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("document_id", selected.get("document_id"));
            item.put("title", selected.get("title"));
            item.put("category", selected.get("category"));
            item.put("material_type", selected.get("material_type"));
            item.put("is_demo", selected.get("is_demo"));
            item.put("visibility", selected.get("visibility"));
            item.put("selected_version_id", selected.get("version_id"));
            item.put("selected_version", selected.get("version"));
            item.put("effective_from", selected.get("effective_from"));
            item.put("lifecycle", selected.get("lifecycle"));
            item.put("index_status", selected.get("index_status"));
            items.add(item);
        }
        items.sort(Comparator.comparing(i -> (String) i.get("title")));
        return Api.slice(items, page, pageSize);
    }

    private static boolean effectiveAt(Map<String, Object> v, OffsetDateTime asOf) {
        OffsetDateTime from = (OffsetDateTime) v.get("effective_from");
        OffsetDateTime until = (OffsetDateTime) v.get("_effective_until");
        return !from.isAfter(asOf) && (until == null || until.isAfter(asOf));
    }

    public Map<String, Object> versions(AuthUser user, String docId, int page, int pageSize) {
        List<Map<String, Object>> all = brandVersions(user, docId);
        if (all.isEmpty()) throw ApiException.notFound();
        List<Map<String, Object>> allowed = all.stream().filter(v -> canRead(user, v)).map(DocumentService::visible).toList();
        if (allowed.isEmpty()) throw ApiException.forbidden();
        return Api.slice(allowed, page, pageSize);
    }

    public Map<String, Object> detail(AuthUser user, String docId, String verId, int page, int pageSize) {
        Map<String, Object> v = loadVersion(user, docId, verId);
        Map<String, Object> sections = Api.slice(sections(v), page, pageSize);
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("version", visible(v));
        data.put("sections", sections.get("data"));
        data.put("sections_meta", sections.get("meta"));
        return data;
    }

    /**
     * 판본의 본문 조각. RAG가 공유 볼륨에 남긴 색인 산출물(revisions/{index_revision}/chunks.jsonl)을 읽는다.
     * 색인 전이거나 파일이 없으면 빈 목록. ponytail: 요청마다 파일을 읽는다(판본당 수백 줄). 느려지면 캐시.
     */
    private List<Map<String, Object>> sections(Map<String, Object> v) {
        if (!(v.get("index_revision") instanceof String revision) || !RagService.ID.matcher(revision).matches()) {
            return List.of();
        }
        Path file = indexDir.resolve("revisions").resolve(revision).resolve("chunks.jsonl");
        if (!Files.isRegularFile(file)) return List.of();
        try {
            List<Map<String, Object>> out = new ArrayList<>();
            for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
                if (line.isBlank()) continue;
                JsonNode chunk = json.readTree(line);
                JsonNode m = chunk.path("metadata");
                if (!v.get("version_id").equals(m.path("version_id").asString(""))) continue; // 다른 판본 산출물 방어
                Map<String, Object> section = new LinkedHashMap<>();
                section.put("chunk_id", m.path("chunk_id").asString());
                section.put("page", m.path("page").isNumber() ? m.path("page").asInt() : null);
                section.put("article", m.path("article").isString() ? m.path("article").asString() : null);
                section.put("heading", chunk.path("heading").asString(""));
                section.put("text", chunk.path("text").asString(""));
                section.put("effective_from", m.path("effective_from").asString());
                section.put("effective_until", m.path("effective_until").isString() ? m.path("effective_until").asString() : null);
                out.add(section);
            }
            return out;
        } catch (IOException | JacksonException e) {
            log.warn("sections unreadable revision={} error={}", revision, e.getMessage());
            return List.of();
        }
    }

    public record StoredFile(Path path, String mediaType, String fileName) {
    }

    public StoredFile file(AuthUser user, String docId, String verId) {
        Map<String, Object> v = loadVersion(user, docId, verId);
        Path path = storeDir.resolve((String) v.get("_storage_key")).normalize();
        if (!path.startsWith(storeDir) || !Files.isRegularFile(path)) throw ApiException.notFound();
        return new StoredFile(path, (String) v.get("media_type"), (String) v.get("file_name"));
    }

    // ---------------------------------------------------------------- 등록

    public ResponseEntity<Object> upload(AuthUser user, String key, MultipartFile file, String metadataJson) {
        user.requireHq("document:write");
        Idempotency.validateKey(key);
        UploadMetadata meta = parseMetadata(metadataJson);
        if (file == null || file.isEmpty()) throw ApiException.invalid("file", "required", "파일을 선택해 주세요.");
        if (file.getSize() > MAX_FILE_BYTES) {
            throw new ApiException(HttpStatus.CONTENT_TOO_LARGE, "FILE_TOO_LARGE", "파일은 10MB 이하만 올릴 수 있습니다.");
        }
        byte[] bytes;
        try {
            bytes = file.getBytes();
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
        String fileName = file.getOriginalFilename() == null ? "upload" : Path.of(file.getOriginalFilename()).getFileName().toString();
        Detected type = detect(fileName, bytes);
        String sha = Idempotency.sha256(bytes);

        Map<String, Object> fingerprint = Map.of("metadata", meta, "sha256", sha, "file_name", fileName);
        return idem.run(user.id(), "upload-document", key, fingerprint, () -> tx.execute(s -> {
            String docId = meta.documentId() != null ? meta.documentId() : "doc-" + shortId();
            String brand = db.sql("SELECT brand_id FROM documents WHERE document_id = :d").param("d", docId)
                    .query(String.class).optional().orElse(null);
            if (brand == null) {
                // ponytail: document_id를 지정했는데 없으면 그 ID로 새 문서를 만든다(시연 자료 ID 고정용).
                db.sql("INSERT INTO documents (document_id, brand_id, created_at) VALUES (:d, :b, :t)")
                        .param("d", docId).param("b", user.brandId()).param("t", Api.now()).update();
            } else if (!brand.equals(user.brandId())) {
                throw ApiException.forbidden();
            }
            boolean taken = db.sql("SELECT COUNT(*) FROM document_versions WHERE document_id = :d AND version = :v")
                    .param("d", docId).param("v", meta.version()).query(Long.class).single() > 0;
            if (taken) throw ApiException.conflict("VERSION_CONFLICT", "같은 판본 이름이 이미 등록되어 있습니다.");

            String verId = docId + "-" + meta.version();
            boolean idTaken = db.sql("SELECT COUNT(*) FROM document_versions WHERE version_id = :v")
                    .param("v", verId).query(Long.class).single() > 0;
            if (idTaken || !RagService.ID.matcher(verId).matches()) verId = docId + "-" + shortId();
            String storageKey = "documents/" + docId + "/" + verId + "." + type.ext;
            write(storageKey, bytes);

            db.sql("""
                    INSERT INTO document_versions (version_id, document_id, version, title, category, material_type,
                      is_demo, visibility, effective_from, lifecycle, index_status, change_summary, original_source,
                      indexing_policy, file_name, media_type, file_size_bytes, sha256, page_count, storage_key,
                      created_by, created_at)
                    VALUES (:vid, :d, :ver, :title, :cat, :mt, :demo, :vis, :from, 'draft', 'not_started', :summary,
                      :src, :policy, :fname, :media, :size, :sha, :pages, :key, :by, :now)""")
                    .param("vid", verId).param("d", docId).param("ver", meta.version())
                    .param("title", meta.title()).param("cat", meta.category()).param("mt", meta.materialType())
                    .param("demo", meta.isDemo()).param("vis", meta.visibility())
                    .param("from", Api.ts(meta.effectiveFrom())).param("summary", meta.changeSummary())
                    .param("src", json.writeValueAsString(meta.originalSource()))
                    .param("policy", json.writeValueAsString(meta.indexingPolicy()))
                    .param("fname", fileName).param("media", type.mediaType).param("size", (long) bytes.length)
                    .param("sha", sha).param("pages", type.pageCount).param("key", storageKey)
                    .param("by", user.id()).param("now", Api.now())
                    .update();
            Map<String, Object> created = loadVersion(user, docId, verId);
            return ResponseEntity.status(HttpStatus.CREATED)
                    .header("Location", "/api/v1/documents/" + docId + "/versions/" + verId)
                    .body((Object) Api.ok(visible(created)));
        }));
    }

    private UploadMetadata parseMetadata(String metadataJson) {
        if (metadataJson == null || metadataJson.isBlank()) {
            throw ApiException.invalid("metadata", "required", "metadata가 필요합니다.");
        }
        UploadMetadata meta;
        try {
            meta = json.readValue(metadataJson, UploadMetadata.class);
        } catch (UnrecognizedPropertyException e) {
            throw ApiException.invalid("metadata." + e.getPropertyName(), "not_allowed", "허용되지 않은 필드입니다: " + e.getPropertyName());
        } catch (JacksonException e) {
            throw ApiException.invalid("metadata", "invalid", "metadata 형식이 올바르지 않습니다.");
        }
        Set<ConstraintViolation<UploadMetadata>> violations = validator.validate(meta);
        if (!violations.isEmpty()) {
            List<Map<String, String>> details = violations.stream()
                    .map(v -> Map.of("field", "metadata." + snake(v.getPropertyPath().toString()),
                            "code", "invalid", "message", v.getMessage()))
                    .toList();
            throw new ApiException(HttpStatus.UNPROCESSABLE_CONTENT, "VALIDATION_ERROR", "입력값을 확인해 주세요.", details, false);
        }
        return meta;
    }

    private record Detected(String mediaType, String ext, Integer pageCount) {
    }

    /** 확장자가 아니라 실제 바이트로 형식을 확인한다. */
    private static Detected detect(String fileName, byte[] bytes) {
        String lower = fileName.toLowerCase();
        if (bytes.length >= 5 && new String(bytes, 0, 5, StandardCharsets.US_ASCII).equals("%PDF-")) {
            try (PDDocument pdf = Loader.loadPDF(bytes)) {
                return new Detected("application/pdf", "pdf", pdf.getNumberOfPages());
            } catch (IOException e) {
                throw unsupported("PDF를 읽을 수 없습니다. 암호화되었거나 손상된 파일인지 확인해 주세요.");
            }
        }
        boolean md = lower.endsWith(".md") || lower.endsWith(".markdown");
        if ((md || lower.endsWith(".txt")) && isUtf8(bytes)) {
            return md ? new Detected("text/markdown", "md", null) : new Detected("text/plain", "txt", null);
        }
        throw unsupported("PDF, TXT, Markdown 파일만 올릴 수 있습니다.");
    }

    private static boolean isUtf8(byte[] bytes) {
        try {
            StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes));
            return true;
        } catch (CharacterCodingException e) {
            return false;
        }
    }

    private static ApiException unsupported(String message) {
        return new ApiException(HttpStatus.UNSUPPORTED_MEDIA_TYPE, "UNSUPPORTED_MEDIA_TYPE", message);
    }

    private void write(String storageKey, byte[] bytes) {
        try {
            Path path = storeDir.resolve(storageKey);
            Files.createDirectories(path.getParent());
            Files.write(path, bytes);
        } catch (IOException e) {
            throw new IllegalStateException("document store write failed", e);
        }
    }

    // ---------------------------------------------------------------- 승인·색인

    public ResponseEntity<Object> approve(AuthUser user, String key, String docId, String verId, Object req, String reviewNote) {
        user.requireHq("document:approve");
        return idem.run(user.id(), "approve:" + docId + "/" + verId, key, req, () -> {
            loadVersion(user, docId, verId);
            String jobId = Api.newId("job");
            tx.executeWithoutResult(s -> {
                int n = db.sql("""
                        UPDATE document_versions SET lifecycle = 'approved', index_status = 'queued',
                          approved_by = :u, approved_at = :now, review_note = :note
                        WHERE version_id = :v AND lifecycle = 'draft'""")
                        .param("u", user.id()).param("now", Api.now()).param("note", reviewNote).param("v", verId)
                        .update();
                if (n == 0) throw ApiException.conflict("INVALID_STATE", "검수 대기(draft) 판본만 승인할 수 있습니다.");
                db.sql("INSERT INTO index_jobs (job_id, version_id, status, created_at, updated_at) "
                                + "VALUES (:j, :v, 'queued', :now, :now)")
                        .param("j", jobId).param("v", verId).param("now", Api.now()).update();
            });
            syncJob(jobId); // RAG가 꺼져 있어도 승인은 유지한다. 작업 조회 때 다시 제출한다.
            String status = db.sql("SELECT index_status FROM document_versions WHERE version_id = :v").param("v", verId)
                    .query(String.class).single();
            Map<String, Object> data = new LinkedHashMap<>();
            data.put("document_id", docId);
            data.put("version_id", verId);
            data.put("lifecycle", "approved");
            data.put("index_status", status);
            data.put("job_id", jobId);
            return ResponseEntity.status(HttpStatus.ACCEPTED).body((Object) Api.ok(data));
        });
    }

    public Map<String, Object> indexJob(AuthUser user, String jobId) {
        user.requireHq("document:write");
        String brand = db.sql("""
                        SELECT d.brand_id FROM index_jobs j
                        JOIN document_versions v ON v.version_id = j.version_id
                        JOIN documents d ON d.document_id = v.document_id WHERE j.job_id = :j""")
                .param("j", jobId).query(String.class).optional().orElseThrow(ApiException::notFound);
        if (!brand.equals(user.brandId())) throw ApiException.notFound();
        syncJob(jobId);
        return db.sql("""
                        SELECT j.*, v.document_id FROM index_jobs j
                        JOIN document_versions v ON v.version_id = j.version_id WHERE j.job_id = :j""")
                .param("j", jobId)
                .query((rs, n) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("job_id", rs.getString("job_id"));
                    m.put("document_id", rs.getString("document_id"));
                    m.put("version_id", rs.getString("version_id"));
                    m.put("status", rs.getString("status"));
                    m.put("chunk_count", rs.getInt("chunk_count"));
                    m.put("index_revision", rs.getString("index_revision"));
                    m.put("error_code", rs.getString("error_code"));
                    m.put("error_message", rs.getString("error_message"));
                    m.put("created_at", Api.kst(rs, "created_at"));
                    m.put("updated_at", Api.kst(rs, "updated_at"));
                    return m;
                }).single();
    }

    /**
     * 끝나지 않은 작업을 RAG에 (재)제출해 상태를 받아온다. job_id가 RAG 쪽 멱등키라 반복 호출해도
     * 같은 작업의 현재 상태가 돌아온다. ponytail: 프론트 폴링(2초)이 동기화 주기. 백그라운드 워커가 필요하면 @Scheduled로 옮긴다.
     */
    private void syncJob(String jobId) {
        Map<String, Object> job = db.sql("SELECT status, attempt FROM index_jobs WHERE job_id = :j").param("j", jobId)
                .query((rs, n) -> Map.<String, Object>of("status", rs.getString("status"), "attempt", rs.getInt("attempt")))
                .single();
        if (Set.of("succeeded", "failed").contains(job.get("status"))) return;
        int attempt = (int) job.get("attempt");
        Map<String, Object> v = db.sql(VERSION_SQL + "JOIN index_jobs j ON j.version_id = v.version_id WHERE j.job_id = :j")
                .param("j", jobId).query(versionMapper()).single();

        Map<String, Object> source = new LinkedHashMap<>();
        source.put("storage_key", v.get("_storage_key"));
        source.put("sha256", v.get("sha256"));
        source.put("media_type", v.get("media_type"));
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("job_id", attempt == 0 ? jobId : jobId + "-r" + attempt); // RAG는 job_id별로 한 번만 실행한다
        for (String k : List.of("brand_id", "document_id", "version_id", "title", "version", "category",
                "material_type", "is_demo", "visibility")) {
            body.put(k, v.get(k));
        }
        body.put("effective_from", DateTimeFormatter.ISO_OFFSET_DATE_TIME.format((OffsetDateTime) v.get("effective_from")));
        body.put("original_source", v.get("original_source"));
        body.put("indexing_policy", v.get("indexing_policy"));
        body.put("source", source);

        JsonNode data;
        try {
            data = rag.submitIndexJob(body);
        } catch (RagClient.Rejected e) {
            // 같은 요청은 다시 보내도 거부된다. 실패로 확정하고, 고친 내용은 새 판본·새 job_id로 색인한다.
            log.warn("index job rejected job_id={} error={}", jobId, e.getMessage());
            record(jobId, (String) v.get("version_id"), "failed", 0, null, "VALIDATION_ERROR", e.getMessage());
            return;
        } catch (RuntimeException e) {
            // 연결 장애·타임아웃 등 일시 실패: 상태를 그대로 두고 다음 조회 때 다시 제출한다.
            log.warn("index job submit failed job_id={} error={}", jobId, e.getMessage());
            return;
        }
        String status = data.path("status").asString("queued");
        if (!Set.of("queued", "running", "succeeded", "failed").contains(status)) return;
        String errorCode = data.path("error_code").isString() ? data.path("error_code").asString() : null;
        if ("failed".equals(status) && "INTERRUPTED".equals(errorCode) && attempt < MAX_INTERRUPTED_RETRIES) {
            // RAG 재시작으로 중단됐을 뿐 내용 문제는 아니다. 공개 job_id는 유지하고 다음 조회 때 새 RAG job_id로 다시 제출한다.
            log.warn("index job interrupted job_id={} attempt={}", jobId, attempt);
            db.sql("UPDATE index_jobs SET attempt = attempt + 1 WHERE job_id = :j").param("j", jobId).update();
            record(jobId, (String) v.get("version_id"), "queued", 0, null, null, null);
            return;
        }
        record(jobId, (String) v.get("version_id"), status, data.path("chunk_count").asInt(0),
                data.path("index_revision").isString() ? data.path("index_revision").asString() : null,
                errorCode,
                data.path("error_message").isString() ? data.path("error_message").asString() : null);
    }

    /** RAG가 계속 재시작되면 무한 재제출하지 않도록 상한을 둔다. 넘으면 failed/INTERRUPTED로 남는다. */
    private static final int MAX_INTERRUPTED_RETRIES = 3;

    private void record(String jobId, String versionId, String status, int chunkCount, String revision,
                        String errorCode, String errorMessage) {
        String indexStatus = "succeeded".equals(status) ? "ready" : status;
        tx.executeWithoutResult(s -> {
            db.sql("""
                    UPDATE index_jobs SET status = :s, chunk_count = :c, index_revision = :r, error_code = :ec,
                      error_message = :em, updated_at = :now WHERE job_id = :j""")
                    .param("s", status).param("c", chunkCount).param("r", revision)
                    .param("ec", errorCode).param("em", errorMessage)
                    .param("now", Api.now()).param("j", jobId).update();
            db.sql("UPDATE document_versions SET index_status = :is, index_revision = :r WHERE version_id = :v")
                    .param("is", indexStatus).param("r", revision).param("v", versionId).update();
        });
    }

    // ---------------------------------------------------------------- 배포

    public ResponseEntity<Object> publish(AuthUser user, String key, String docId, String verId, Object req, String noticeTitle) {
        user.requireHq("document:publish");
        return idem.run(user.id(), "publish:" + docId + "/" + verId, key, req, () -> tx.execute(s -> {
            // 같은 문서의 동시 배포를 직렬화
            db.sql("SELECT document_id FROM documents WHERE document_id = :d FOR UPDATE").param("d", docId)
                    .query(String.class).optional().orElseThrow(ApiException::notFound);
            Map<String, Object> v = loadVersion(user, docId, verId);
            if (!"approved".equals(v.get("lifecycle"))) {
                throw ApiException.conflict("INVALID_STATE", "승인된 판본만 배포할 수 있습니다.");
            }
            if (!"ready".equals(v.get("index_status"))) {
                throw ApiException.conflict("INDEX_NOT_READY", "색인이 끝난 뒤 배포할 수 있습니다.");
            }
            var from = Api.ts((OffsetDateTime) v.get("effective_from"));
            long overlapping = db.sql("SELECT COUNT(*) FROM document_versions WHERE document_id = :d "
                            + "AND lifecycle = 'published' AND effective_from >= :f")
                    .param("d", docId).param("f", from).query(Long.class).single();
            if (overlapping > 0) {
                throw ApiException.conflict("EFFECTIVE_DATE_CONFLICT", "같은 적용 시각 이후에 이미 배포된 판본이 있습니다.");
            }
            var now = Api.now();
            // 직전 판본은 새 판본 적용 시각까지만 유효(이력은 보존)
            db.sql("UPDATE document_versions SET effective_until = :f WHERE document_id = :d AND lifecycle = 'published' "
                            + "AND (effective_until IS NULL OR effective_until > :f)")
                    .param("f", from).param("d", docId).update();
            db.sql("UPDATE document_versions SET lifecycle = 'published', published_at = :now, published_by = :u "
                            + "WHERE version_id = :v")
                    .param("now", now).param("u", user.id()).param("v", verId).update();

            String noticeId = Api.newId("notice");
            db.sql("INSERT INTO notices (notice_id, brand_id, document_id, version_id, title, published_at) "
                            + "VALUES (:n, :b, :d, :v, :t, :now)")
                    .param("n", noticeId).param("b", user.brandId()).param("d", docId).param("v", verId)
                    .param("t", noticeTitle).param("now", now).update();
            if (!"hq".equals(v.get("visibility"))) {
                // 대상 점포는 배포 시점에 고정
                db.sql("INSERT INTO notice_targets (notice_id, store_id) "
                                + "SELECT :n, store_id FROM stores WHERE brand_id = :b AND active = TRUE")
                        .param("n", noticeId).param("b", user.brandId()).update();
            }
            Map<String, Object> data = new LinkedHashMap<>();
            data.put("document_id", docId);
            data.put("version_id", verId);
            data.put("lifecycle", "published");
            data.put("index_status", "ready");
            data.put("effective_from", v.get("effective_from"));
            data.put("published_at", Api.kst(now.toInstant()));
            data.put("notice_id", noticeId);
            return ResponseEntity.ok((Object) Api.ok(data));
        }));
    }

    private static String shortId() {
        return UUID.randomUUID().toString().substring(0, 8);
    }

    private static String snake(String field) {
        return field.replaceAll("([a-z])([A-Z])", "$1_$2").toLowerCase();
    }
}
