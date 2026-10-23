package com.frangizer.auth;

import java.io.IOException;
import java.util.Optional;

import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import com.frangizer.common.ApiException;
import com.frangizer.common.ErrorHandler;

import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.JwtException;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import tools.jackson.databind.ObjectMapper;

/** /api/v1/* 중 로그인·상태 확인을 제외한 모든 요청에 Bearer JWT를 요구한다. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public class AuthFilter extends OncePerRequestFilter {

    /** 내부 RAG 호출 시 원 JWT를 그대로 넘기기 위해 보관한다. */
    public static final String TOKEN_ATTR = "authToken";

    private final JwtService jwt;
    private final AuthService auth;
    private final ObjectMapper json;

    AuthFilter(JwtService jwt, AuthService auth, ObjectMapper json) {
        this.jwt = jwt;
        this.auth = auth;
        this.json = json;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI().substring(request.getContextPath().length());
        return !path.startsWith("/api/v1/")
                || path.equals("/api/v1/login")
                || path.equals("/api/v1/health")
                || "OPTIONS".equals(request.getMethod());
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String header = request.getHeader("Authorization");
        if (header == null || !header.startsWith("Bearer ")) {
            reject(request, response, "AUTH_REQUIRED", "로그인이 필요합니다.");
            return;
        }
        String token = header.substring(7).trim();
        Optional<AuthUser> user;
        try {
            user = auth.findById(jwt.verify(token).getSubject());
        } catch (ExpiredJwtException e) {
            reject(request, response, "TOKEN_EXPIRED", "로그인이 만료되었습니다. 다시 로그인해 주세요.");
            return;
        } catch (JwtException | IllegalArgumentException e) {
            reject(request, response, "AUTH_REQUIRED", "로그인이 필요합니다.");
            return;
        }
        if (user.isEmpty()) {
            reject(request, response, "AUTH_REQUIRED", "로그인이 필요합니다.");
            return;
        }
        request.setAttribute(AuthUser.ATTR, user.get());
        request.setAttribute(TOKEN_ATTR, token);
        chain.doFilter(request, response);
    }

    private void reject(HttpServletRequest request, HttpServletResponse response, String code, String message) throws IOException {
        response.setStatus(401);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding("UTF-8");
        var body = ErrorHandler.body(new ApiException(HttpStatus.UNAUTHORIZED, code, message));
        body.put("request_id", request.getAttribute(com.frangizer.common.Api.REQUEST_ID)); // RequestContext 이전 단계
        json.writeValue(response.getOutputStream(), body);
    }
}
