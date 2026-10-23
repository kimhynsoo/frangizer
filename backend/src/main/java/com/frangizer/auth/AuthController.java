package com.frangizer.auth;

import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import com.frangizer.common.Api;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

@RestController
@RequestMapping("/api/v1")
public class AuthController {

    record LoginRequest(@NotNull @Size(min = 3, max = 50) String username,
                        @NotNull @Size(min = 1, max = 128) String password) {
    }

    private final AuthService auth;
    private final JwtService jwt;

    AuthController(AuthService auth, JwtService jwt) {
        this.auth = auth;
        this.jwt = jwt;
    }

    /** 교재 호환: data로 감싸지 않고 accessToken을 최상위에 둔다. */
    @PostMapping("/login")
    Map<String, Object> login(@Valid @RequestBody LoginRequest req) {
        AuthUser user = auth.authenticate(req.username(), req.password());
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("accessToken", jwt.issue(user));
        body.put("token_type", "Bearer");
        body.put("expires_in", jwt.ttlSeconds);
        body.put("user", user);
        body.put("request_id", Api.requestId());
        return body;
    }

    @GetMapping("/me")
    Map<String, Object> me(@RequestAttribute(AuthUser.ATTR) AuthUser user) {
        return Api.ok(user);
    }
}
