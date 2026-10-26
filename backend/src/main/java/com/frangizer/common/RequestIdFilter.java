package com.frangizer.common;

import java.io.IOException;

import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

/**
 * 모든 응답에 X-Request-ID를 붙이고, 요청마다 경로·상태·지연을 한 줄 남긴다.
 * RAG를 거친 요청은 같은 줄에 토큰 사용량과 추정 비용을 붙인다({@link RagUsage}).
 * 질문 원문·본문·쿼리 문자열은 개인정보가 있을 수 있어 남기지 않는다.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 1)
public class RequestIdFilter extends OncePerRequestFilter {

    private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(RequestIdFilter.class);

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String id = Api.newId("req");
        request.setAttribute(Api.REQUEST_ID, id);
        response.setHeader("X-Request-ID", id);
        long started = System.nanoTime();
        try {
            chain.doFilter(request, response);
        } finally {
            if (request.getRequestURI().startsWith("/api/")) {
                Object user = request.getAttribute("authUser");
                Object usage = request.getAttribute(RagUsage.ATTRIBUTE);
                log.info("request_id={} method={} path={} status={} latency_ms={} user={}{}", id, request.getMethod(),
                        request.getRequestURI(), response.getStatus(), (System.nanoTime() - started) / 1_000_000,
                        user instanceof com.frangizer.auth.AuthUser u ? u.id() : "-",
                        usage instanceof RagUsage r ? " " + r.logFields() : "");
            }
        }
    }
}
