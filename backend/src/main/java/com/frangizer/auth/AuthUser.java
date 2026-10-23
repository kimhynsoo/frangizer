package com.frangizer.auth;

import java.util.List;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.frangizer.common.ApiException;

/** 인증된 사용자. 역할·브랜드·점포는 JWT 주체로 DB에서 다시 읽은 값이다. 명세의 User 스키마로 직렬화된다. */
public record AuthUser(String id, String username, String role, String brandId, String storeId,
                       String regionCode, List<String> permissions) {

    public static final String ATTR = "authUser";

    public void require(String permission) {
        if (!permissions.contains(permission)) throw ApiException.forbidden();
    }

    /** hq 전용 권한 검사. */
    public void requireHq(String permission) {
        if (!isHq()) throw ApiException.forbidden();
        require(permission);
    }

    @JsonIgnore
    public boolean isHq() {
        return "hq".equals(role);
    }

    /** 초안·승인 판본을 볼 수 있는 본사 관리 권한. */
    @JsonIgnore
    public boolean canManageDocuments() {
        return isHq() && (permissions.contains("document:write") || permissions.contains("document:approve")
                || permissions.contains("document:publish"));
    }

    /** visibility는 최소 허용 역할: prospect < owner < hq. */
    public boolean canSee(String visibility) {
        return rank(role) >= 0 && rank(visibility) >= 0 && rank(role) >= rank(visibility);
    }

    private static int rank(String role) {
        return switch (role) {
            case "prospect" -> 0;
            case "owner" -> 1;
            case "hq" -> 2;
            default -> -1; // 알 수 없는 역할은 아무것도 보지 못한다
        };
    }
}
