package com.frangizer.analytics;

import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.frangizer.auth.AuthUser;
import com.frangizer.common.Api;

@RestController
@RequestMapping("/api/v1")
public class AnalyticsController {

    private final AnalyticsService analytics;

    AnalyticsController(AnalyticsService analytics) {
        this.analytics = analytics;
    }

    @GetMapping("/regions")
    Map<String, Object> regions(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                                @RequestParam(defaultValue = "1") int page,
                                @RequestParam(name = "page_size", defaultValue = "20") int pageSize) {
        return analytics.regions(user, page, pageSize);
    }

    @GetMapping("/stores")
    Map<String, Object> stores(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                               @RequestParam(name = "region_code", required = false) String regionCode,
                               @RequestParam(defaultValue = "1") int page,
                               @RequestParam(name = "page_size", defaultValue = "20") int pageSize) {
        return analytics.stores(user, regionCode, page, pageSize);
    }

    @GetMapping("/topics")
    Map<String, Object> topics(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                               @RequestParam(required = false) String category,
                               @RequestParam(defaultValue = "1") int page,
                               @RequestParam(name = "page_size", defaultValue = "20") int pageSize) {
        return analytics.topics(user, category, page, pageSize);
    }

    @GetMapping("/analytics/questions")
    Map<String, Object> questions(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                                  @RequestParam Map<String, String> params) {
        return Api.ok(analytics.questions(user, query(params)));
    }

    @GetMapping("/analytics/question-events")
    Map<String, Object> events(@RequestAttribute(AuthUser.ATTR) AuthUser user,
                               @RequestParam Map<String, String> params,
                               @RequestParam(defaultValue = "1") int page,
                               @RequestParam(name = "page_size", defaultValue = "20") int pageSize) {
        return analytics.events(user, query(params), params.get("answer_status"), params.get("resolution_status"),
                params.get("classification_status"), page, pageSize);
    }

    @GetMapping("/analytics/question-events/{questionId}")
    Map<String, Object> event(@RequestAttribute(AuthUser.ATTR) AuthUser user, @PathVariable String questionId) {
        return Api.ok(analytics.event(user, questionId));
    }

    @PatchMapping("/analytics/question-events/{questionId}")
    Map<String, Object> patch(@RequestAttribute(AuthUser.ATTR) AuthUser user, @PathVariable String questionId,
                              @RequestBody Map<String, Object> body) {
        return Api.ok(analytics.patch(user, questionId, body));
    }

    private static AnalyticsService.Query query(Map<String, String> p) {
        return new AnalyticsService.Query(p.get("week_start"), p.get("from"), p.get("to"), p.get("region_code"),
                p.get("store_id"), p.get("category"), p.get("topic_id"), p.get("user_role"));
    }
}
