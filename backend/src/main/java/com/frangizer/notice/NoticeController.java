package com.frangizer.notice;

import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.frangizer.auth.AuthUser;
import com.frangizer.common.Api;
import com.frangizer.common.ApiException;

@RestController
@RequestMapping("/api/v1/notices")
public class NoticeController {

    private final NoticeService notices;

    NoticeController(NoticeService notices) {
        this.notices = notices;
    }

    @GetMapping
    Map<String, Object> list(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                             @RequestParam(defaultValue = "false") boolean unread,
                             @RequestParam(defaultValue = "1") int page,
                             @RequestParam(name = "page_size", defaultValue = "20") int pageSize) {
        return notices.list(user, unread, page, pageSize);
    }

    @PutMapping("/{noticeId}/ack")
    Map<String, Object> ack(@RequestAttribute(AuthUser.ATTR) AuthUser user, @PathVariable String noticeId,
                            @RequestBody(required = false) Map<String, Object> body) {
        if (body != null && !body.isEmpty()) {
            String field = body.keySet().iterator().next();
            throw ApiException.invalid(field, "not_allowed", "허용되지 않은 필드입니다: " + field);
        }
        return Api.ok(notices.acknowledge(user, noticeId));
    }

    @GetMapping("/{noticeId}/acknowledgements")
    Map<String, Object> acknowledgements(@RequestAttribute(AuthUser.ATTR) AuthUser user, @PathVariable String noticeId,
                                         @RequestParam(required = false) Boolean acknowledged,
                                         @RequestParam(name = "region_code", required = false) String regionCode,
                                         @RequestParam(defaultValue = "1") int page,
                                         @RequestParam(name = "page_size", defaultValue = "20") int pageSize) {
        return notices.acknowledgements(user, noticeId, acknowledged, regionCode, page, pageSize);
    }
}
