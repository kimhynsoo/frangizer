package com.frangizer.document;

import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.Map;

import org.springframework.core.io.FileSystemResource;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import com.frangizer.auth.AuthUser;
import com.frangizer.common.Api;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

@RestController
@RequestMapping("/api/v1")
public class DocumentController {

    record ApprovalRequest(@NotNull @Size(min = 1, max = 2000) String reviewNote) {
    }

    record PublishRequest(@NotNull @Size(min = 1, max = 200) String noticeTitle) {
    }

    private final DocumentService documents;

    DocumentController(DocumentService documents) {
        this.documents = documents;
    }

    @GetMapping("/documents")
    Map<String, Object> list(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                             @RequestParam(required = false) String category,
                             @RequestParam(name = "as_of", required = false)
                             @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) OffsetDateTime asOf,
                             @RequestParam(defaultValue = "false") boolean management,
                             @RequestParam(defaultValue = "1") int page,
                             @RequestParam(name = "page_size", defaultValue = "20") int pageSize) {
        return documents.list(user, category, asOf, management, page, pageSize);
    }

    @PostMapping(path = "/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    ResponseEntity<Object> upload(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                                  @RequestHeader("Idempotency-Key") String key,
                                  @RequestPart("file") MultipartFile file,
                                  @RequestPart("metadata") String metadata) {
        return documents.upload(user, key, file, metadata);
    }

    @GetMapping("/documents/{docId}/versions")
    Map<String, Object> versions(@RequestAttribute(AuthUser.ATTR) AuthUser user, @PathVariable String docId,
                                 @RequestParam(defaultValue = "1") int page,
                                 @RequestParam(name = "page_size", defaultValue = "20") int pageSize) {
        return documents.versions(user, docId, page, pageSize);
    }

    @GetMapping("/documents/{docId}/versions/{verId}")
    Map<String, Object> detail(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                               @PathVariable String docId, @PathVariable String verId,
                               @RequestParam(defaultValue = "1") int page,
                               @RequestParam(name = "page_size", defaultValue = "20") int pageSize) {
        return Api.ok(documents.detail(user, docId, verId, page, pageSize));
    }

    @GetMapping("/documents/{docId}/versions/{verId}/file")
    ResponseEntity<FileSystemResource> file(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                                            @PathVariable String docId, @PathVariable String verId) {
        DocumentService.StoredFile f = documents.file(user, docId, verId);
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType(f.mediaType()))
                .header(HttpHeaders.CONTENT_DISPOSITION, ContentDisposition.attachment()
                        .filename(f.fileName(), StandardCharsets.UTF_8).build().toString())
                .body(new FileSystemResource(f.path()));
    }

    @PostMapping("/documents/{docId}/versions/{verId}/approve")
    ResponseEntity<Object> approve(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                                   @RequestHeader("Idempotency-Key") String key,
                                   @PathVariable String docId, @PathVariable String verId,
                                   @Valid @RequestBody ApprovalRequest req) {
        return documents.approve(user, key, docId, verId, req, req.reviewNote());
    }

    @GetMapping("/index-jobs/{jobId}")
    Map<String, Object> indexJob(@RequestAttribute(AuthUser.ATTR) AuthUser user, @PathVariable String jobId) {
        return Api.ok(documents.indexJob(user, jobId));
    }

    @PostMapping("/documents/{docId}/versions/{verId}/publish")
    ResponseEntity<Object> publish(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                                   @RequestHeader("Idempotency-Key") String key,
                                   @PathVariable String docId, @PathVariable String verId,
                                   @Valid @RequestBody PublishRequest req) {
        return documents.publish(user, key, docId, verId, req, req.noticeTitle());
    }
}
