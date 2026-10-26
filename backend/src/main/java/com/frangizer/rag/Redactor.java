package com.frangizer.rag;

import java.util.regex.Pattern;

/** 질문 속 전화번호·이메일·주문번호·고객명을 가린다. 원문은 저장·전달하지 않는다. */
public final class Redactor {

    private static final Pattern PHONE = Pattern.compile("(?<!\\d)(01[016789]|02|0[3-6][1-5])[-.\\s]?\\d{3,4}[-.\\s]?\\d{4}(?!\\d)");
    private static final Pattern EMAIL = Pattern.compile("[\\w.+-]+@[\\w-]+(\\.[\\w-]+)+");
    private static final Pattern ORDER = Pattern.compile("(주문\\s*번호|주문|영수증\\s*번호)(\\s*[:은는이가]?\\s*)[A-Za-z0-9-]{4,}");
    private static final Pattern LONG_NUMBER = Pattern.compile("(?<![\\d.])\\d{8,}(?![\\d.])");
    // ponytail: 이름은 이름이라는 신호가 분명한 꼴만 잡는다. 문맥 없는 이름까지 잡으려면 NER이 필요하다.
    // '고객이 맛이', '손님이 음료에'처럼 조사 뒤의 단어는 이름이 아니므로 가리지 않는다(질문 뜻이 바뀌어 검색을 망친다).
    private static final String HANGUL_NAME = "[가-힣]{2,4}";
    /** 고객 홍길동 님, 손님 김철수씨: 호칭(님·씨)이 붙은 이름. */
    private static final Pattern NAME_HONORIFIC = Pattern.compile("(고객|손님)(\\s+)" + HANGUL_NAME + "(?=\\s*(님|씨))");
    /** 고객명: 홍길동, 성함 : 이영희 */
    private static final Pattern NAME_LABEL = Pattern.compile("(고객명|고객\\s*이름|성함)(\\s*[:：]\\s*)" + HANGUL_NAME + "(?=$|[\\s,.!?]|님|씨)");
    /** 성함은 이영희입니다, 고객명은 홍길동님: 서술·호칭이 이어질 때만. '성함은 어떻게 되나요'는 가리지 않는다. */
    private static final Pattern NAME_TOPIC = Pattern.compile("(고객명|고객\\s*이름|성함)(\\s*[은는]\\s*)" + HANGUL_NAME
            + "(?=\\s*(님|씨|입니다|이에요|예요)|$|[,.!?])");

    private Redactor() {
    }

    public static String redact(String text) {
        String s = EMAIL.matcher(text).replaceAll("[이메일]");
        s = PHONE.matcher(s).replaceAll("[전화번호]");
        s = ORDER.matcher(s).replaceAll("$1$2[주문번호]");
        s = LONG_NUMBER.matcher(s).replaceAll("[번호]");
        s = NAME_HONORIFIC.matcher(s).replaceAll("$1$2[이름]");
        s = NAME_LABEL.matcher(s).replaceAll("$1$2[이름]");
        s = NAME_TOPIC.matcher(s).replaceAll("$1$2[이름]");
        return s;
    }
}
