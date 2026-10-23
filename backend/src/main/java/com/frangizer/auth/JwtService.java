package com.frangizer.auth;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;

import javax.crypto.SecretKey;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;

/** HS256 JWT. FastAPI RAG도 같은 JWT_SECRET·iss·aud로 재검증한다. */
@Component
public class JwtService {

    private final SecretKey key;
    private final String issuer;
    private final String audience;
    public final long ttlSeconds;

    JwtService(@Value("${app.jwt.secret}") String secret,
               @Value("${app.jwt.issuer}") String issuer,
               @Value("${app.jwt.audience}") String audience,
               @Value("${app.jwt.ttl-seconds}") long ttlSeconds) {
        this.key = Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8)); // 32바이트 미만이면 기동 실패
        this.issuer = issuer;
        this.audience = audience;
        this.ttlSeconds = ttlSeconds;
    }

    public String issue(AuthUser user) {
        Instant now = Instant.now();
        return Jwts.builder()
                .subject(user.id())
                .issuer(issuer)
                .audience().add(audience).and()
                .claim("username", user.username())
                .claim("role", user.role())
                .claim("brand_id", user.brandId())
                .claim("store_id", user.storeId())
                .claim("permissions", user.permissions())
                .issuedAt(Date.from(now))
                .expiration(Date.from(now.plusSeconds(ttlSeconds)))
                .signWith(key, Jwts.SIG.HS256) // 키 길이와 무관하게 HS256 고정(RAG 재검증과 일치)
                .compact();
    }

    /** 서명·만료·iss·aud 검증. 실패하면 jjwt 예외. */
    public Claims verify(String token) {
        return Jwts.parser()
                .verifyWith(key)
                .requireIssuer(issuer)
                .requireAudience(audience)
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }
}
