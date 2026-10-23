package com.frangizer.rag;

import java.util.regex.Pattern;

/** 질문 속 전화번호·이메일·주문번호·고객명을 가린다. 원문은 저장·전달하지 않는다. */
public final class Redactor {

    private static final Pattern PHONE = Pattern.compile("(?<!\\d)(01[016789]|02|0[3-6][1-5])[-.\\s]?\\d{3,4}[-.\\s]?\\d{4}(?!\\d)");
    private static final Pattern EMAIL = Pattern.compile("[\\w.+-]+@[\\w-]+(\\.[\\w-]+)+");
    private static final Pattern ORDER = Pattern.compile("(주문\\s*번호|주문|영수증\\s*번호)(\\s*[:은는이가]?\\s*)[A-Za-z0-9-]{4,}");
    private static final Pattern LONG_NUMBER = Pattern.compile("(?<![\\d.])\\d{8,}(?![\\d.])");
    // ponytail: 이름은 '고객/손님 + 이름' 꼴만 잡는다. 문맥 없는 이름까지 잡으려면 NER이 필요하다.
    private static final Pattern NAME = Pattern.compile("(고객명|고객|손님|성함)(\\s*[:은는이가]?\\s*)[가-힣]{2,4}(?=\\s*(님|씨|고객|$|[\\s,.!?]))");

    private Redactor() {
    }

    public static String redact(String text) {
        String s = EMAIL.matcher(text).replaceAll("[이메일]");
        s = PHONE.matcher(s).replaceAll("[전화번호]");
        s = ORDER.matcher(s).replaceAll("$1$2[주문번호]");
        s = LONG_NUMBER.matcher(s).replaceAll("[번호]");
        s = NAME.matcher(s).replaceAll("$1$2[이름]");
        return s;
    }
}
