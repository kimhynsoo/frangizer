package com.frangizer.auth;

import java.util.List;

/** Uses the real backend JwtService, not a separately reimplemented signer. */
public class JwtInterop {
    public static void main(String[] args) {
        String role = args[0];
        JwtService service = new JwtService("offline-java-interop-key-at-least-32-bytes", "frangizer", "frangizer-api", 120);
        AuthUser user = new AuthUser("test-user", "test-user", role, "brand-test",
                "owner".equals(role) ? "store-test" : null, null, List.of("rag:ask"));
        System.out.print(service.issue(user));
    }
}
