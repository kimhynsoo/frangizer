package com.frangizer.rag;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

/** 이름 마스킹: 이름이 분명한 꼴은 가리고, 조사 뒤 일반 단어는 남긴다(질문 뜻이 바뀌면 검색이 망가진다). */
class RedactorTest {

    @ParameterizedTest
    @CsvSource(delimiter = '|', value = {
            "고객 홍길동 님이 환불을 요청했어요 | 고객 [이름] 님이 환불을 요청했어요",
            "손님 김철수씨가 항의했어요 | 손님 [이름]씨가 항의했어요",
            "고객명: 박민수, 음료 불만 | 고객명: [이름], 음료 불만",
            "고객명 : 이영희 환불 요청 | 고객명 : [이름] 환불 요청",
            "성함은 최지우입니다 | 성함은 [이름]입니다",
    })
    void masksClearNames(String input, String expected) {
        assertThat(Redactor.redact(input)).isEqualTo(expected);
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "고객이 맛이 마음에 안 든다는데 무조건 환불해야 하나요?",
            "고객이 음료에 이물이 있다며 환불을 요구합니다.",
            "손님이 환불을 요구해요",
            "고객이 컵을 떨어뜨렸어요",
            "손님이 메뉴를 바꿔 달래요",
            "고객님이 영수증을 잃어버렸대요",
            "성함은 어떻게 되시나요?",
            "단골 고객님께 서비스를 드려도 되나요?",
    })
    void keepsOrdinaryWords(String input) {
        assertThat(Redactor.redact(input)).isEqualTo(input);
    }
}
