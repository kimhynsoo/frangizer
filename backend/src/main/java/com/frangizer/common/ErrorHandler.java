package com.frangizer.common;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.multipart.support.MissingServletRequestPartException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import tools.jackson.databind.DatabindException;
import tools.jackson.databind.exc.MismatchedInputException;
import tools.jackson.databind.exc.UnrecognizedPropertyException;

/** 모든 오류를 { error: {...}, request_id } 형식으로. 스택트레이스·내부 경로는 노출하지 않는다. */
@RestControllerAdvice
public class ErrorHandler {

    private static final Logger log = LoggerFactory.getLogger(ErrorHandler.class);

    public static Map<String, Object> body(ApiException e) {
        Map<String, Object> error = new LinkedHashMap<>();
        error.put("code", e.code);
        error.put("message", e.getMessage());
        error.put("details", e.details);
        error.put("question_id", e.questionId);
        error.put("retryable", e.retryable);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error", error);
        body.put("request_id", Api.requestId());
        return body;
    }

    @ExceptionHandler(ApiException.class)
    ResponseEntity<Map<String, Object>> api(ApiException e) {
        return ResponseEntity.status(e.status).body(body(e));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    ResponseEntity<Map<String, Object>> invalid(MethodArgumentNotValidException e) {
        List<Map<String, String>> details = e.getBindingResult().getFieldErrors().stream()
                .map(f -> Map.of("field", snake(f.getField()), "code", String.valueOf(f.getCode()).toLowerCase(),
                        "message", String.valueOf(f.getDefaultMessage())))
                .toList();
        return api(new ApiException(HttpStatus.UNPROCESSABLE_CONTENT, "VALIDATION_ERROR",
                "입력값을 확인해 주세요.", details, false));
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    ResponseEntity<Map<String, Object>> unreadable(HttpMessageNotReadableException e) {
        Throwable cause = e.getMostSpecificCause();
        if (cause instanceof UnrecognizedPropertyException u) {
            return api(ApiException.invalid(u.getPropertyName(), "not_allowed", "허용되지 않은 필드입니다: " + u.getPropertyName()));
        }
        if (cause instanceof MismatchedInputException m) {
            return api(ApiException.invalid(path(m), "invalid", "형식이 올바르지 않습니다: " + path(m)));
        }
        return api(new ApiException(HttpStatus.BAD_REQUEST, "MALFORMED_REQUEST", "요청 형식이 올바르지 않습니다."));
    }

    @ExceptionHandler(MissingRequestHeaderException.class)
    ResponseEntity<Map<String, Object>> header(MissingRequestHeaderException e) {
        return api(ApiException.invalid(e.getHeaderName(), "required", e.getHeaderName() + " 헤더가 필요합니다."));
    }

    @ExceptionHandler(MissingServletRequestParameterException.class)
    ResponseEntity<Map<String, Object>> param(MissingServletRequestParameterException e) {
        return api(ApiException.invalid(e.getParameterName(), "required", e.getParameterName() + " 값이 필요합니다."));
    }

    @ExceptionHandler(MissingServletRequestPartException.class)
    ResponseEntity<Map<String, Object>> part(MissingServletRequestPartException e) {
        return api(ApiException.invalid(e.getRequestPartName(), "required", e.getRequestPartName() + " 파트가 필요합니다."));
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    ResponseEntity<Map<String, Object>> type(MethodArgumentTypeMismatchException e) {
        return api(ApiException.invalid(e.getName(), "invalid", e.getName() + " 값이 올바르지 않습니다."));
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    ResponseEntity<Map<String, Object>> tooLarge() {
        return api(new ApiException(HttpStatus.CONTENT_TOO_LARGE, "FILE_TOO_LARGE", "파일은 10MB 이하만 올릴 수 있습니다."));
    }

    @ExceptionHandler(HttpMediaTypeNotSupportedException.class)
    ResponseEntity<Map<String, Object>> mediaType() {
        return api(new ApiException(HttpStatus.UNSUPPORTED_MEDIA_TYPE, "UNSUPPORTED_MEDIA_TYPE", "지원하지 않는 요청 형식입니다."));
    }

    @ExceptionHandler({NoResourceFoundException.class, HttpRequestMethodNotSupportedException.class})
    ResponseEntity<Map<String, Object>> notFound() {
        return api(ApiException.notFound());
    }

    @ExceptionHandler(Exception.class)
    ResponseEntity<Map<String, Object>> unexpected(Exception e) {
        log.error("unhandled request_id={}", Api.requestId(), e);
        return api(new ApiException(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR",
                "일시적인 오류가 발생했습니다. 요청 ID를 담당자에게 알려 주세요."));
    }

    private static String path(DatabindException e) {
        return e.getPath().stream()
                .map(r -> r.getPropertyName() != null ? r.getPropertyName() : String.valueOf(r.getIndex()))
                .reduce((a, b) -> a + "." + b).orElse("body");
    }

    private static String snake(String field) {
        return field.replaceAll("([a-z])([A-Z])", "$1_$2").toLowerCase();
    }
}
