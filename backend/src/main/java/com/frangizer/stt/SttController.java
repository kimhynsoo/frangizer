package com.frangizer.stt;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

import com.frangizer.common.ApiException;

/** POST /stt는 선택 기능. 음성 서비스(:8008) 계약이 정해지면 프록시로 바꾼다. */
@RestController
public class SttController {

    @PostMapping("/api/v1/stt")
    void transcribe() {
        throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "SERVICE_UNAVAILABLE", "음성 질문은 아직 준비되지 않았습니다. 텍스트로 질문해 주세요.");
    }
}
