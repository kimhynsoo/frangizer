package com.frangizer.common;

import java.util.List;
import java.util.Map;

import org.springframework.http.HttpStatus;

/** 명세 10장 오류 응답으로 변환되는 예외. */
public class ApiException extends RuntimeException {

    public final HttpStatus status;
    public final String code;
    public final List<Map<String, String>> details;
    public final boolean retryable;
    public String questionId;

    public ApiException(HttpStatus status, String code, String message, List<Map<String, String>> details, boolean retryable) {
        super(message);
        this.status = status;
        this.code = code;
        this.details = details;
        this.retryable = retryable;
    }

    public ApiException(HttpStatus status, String code, String message) {
        this(status, code, message, List.of(), status.is5xxServerError() || status == HttpStatus.TOO_MANY_REQUESTS);
    }

    public static ApiException invalid(String field, String code, String message) {
        return new ApiException(HttpStatus.UNPROCESSABLE_CONTENT, "VALIDATION_ERROR", message,
                List.of(Map.of("field", field, "code", code, "message", message)), false);
    }

    public static ApiException notFound() {
        return new ApiException(HttpStatus.NOT_FOUND, "RESOURCE_NOT_FOUND", "요청한 항목을 찾을 수 없습니다.");
    }

    public static ApiException forbidden() {
        return new ApiException(HttpStatus.FORBIDDEN, "FORBIDDEN", "접근 권한이 없습니다.");
    }

    public static ApiException conflict(String code, String message) {
        return new ApiException(HttpStatus.CONFLICT, code, message);
    }
}
