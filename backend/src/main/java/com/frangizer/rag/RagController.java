package com.frangizer.rag;

import java.time.OffsetDateTime;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import com.frangizer.auth.AuthFilter;
import com.frangizer.auth.AuthUser;

@RestController
public class RagController {

    /** role·brand_id·store_id·top_k 등은 필드에 없으므로 들어오면 422(알 수 없는 필드). */
    public record AskRequest(String question, String conversationId, OffsetDateTime asOf) {
    }

    private final RagService rag;

    RagController(RagService rag) {
        this.rag = rag;
    }

    @PostMapping("/api/v1/rag-query")
    ResponseEntity<Object> ask(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                               @RequestAttribute(AuthFilter.TOKEN_ATTR) String token,
                               @RequestHeader("Idempotency-Key") String key,
                               @RequestBody AskRequest req) {
        return rag.ask(user, token, key, req);
    }
}
